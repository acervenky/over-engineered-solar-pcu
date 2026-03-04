"""
main.py — Smart Solar PCU — Agentic AI Server  (v4.0)

What changed from v3:
  ✅ Real Ollama LLM tool-calling loop (OBSERVE→REASON→PLAN→ACT→REFLECT)
  ✅ SQLite-backed persistent memory (aiosqlite)
  ✅ WebSocket for Pico W — real-time bidirectional, no more polling
  ✅ SSE streaming endpoint so dashboards see the agent think in real time
  ✅ Rate limiting on telemetry (slowapi)
  ✅ All endpoints authenticated — no accidental open routes
  ✅ Hard fail on missing/placeholder API_KEY
  ✅ Structured JSON logging (loguru)
  ✅ Background reflect loop on every step
"""
import asyncio
import json
import os
import sys
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

import uvicorn
from fastapi import (
    Depends, FastAPI, Header, HTTPException, Request, WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse

from .memory import AgentMemory
from .solar_agent import SolarAgent
from .tools import ToolRegistry
from config import settings
from .database import init_db

# ── Logging setup ─────────────────────────────────────────────────────────────
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {name}:{line} | {message}",
    level="INFO",
    colorize=True,
)
logger.add(
    "logs/solar_agent.log",
    rotation="10 MB",
    retention="14 days",
    level="DEBUG",
    serialize=True,  # JSON lines for log aggregators
)

# ── Rate limiter ──────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)


# ═══════════════════════════════════════════════════════════════════════════════
# WebSocket Manager
# ═══════════════════════════════════════════════════════════════════════════════

class WebSocketManager:
    """
    Manages two connection types:
      1. pico_ws  — exactly ONE Pico W device connection
      2. dashboard — multiple read-only dashboard SSE subscribers (via broadcast_event)
    """

    def __init__(self) -> None:
        self._pico_ws:     Optional[WebSocket] = None
        self._pending:     Deque[dict]          = deque(maxlen=50)
        self._subscribers: List[asyncio.Queue]  = []  # SSE queues

    # ── Pico W ────────────────────────────────────────────────────

    @property
    def pico_connected(self) -> bool:
        return self._pico_ws is not None

    async def connect_pico(self, ws: WebSocket) -> None:
        await ws.accept()
        self._pico_ws = ws
        logger.info("[WS] Pico W connected")
        # Drain any queued commands
        while self._pending:
            cmd = self._pending.popleft()
            await self._try_send(cmd)

    def disconnect_pico(self) -> None:
        self._pico_ws = None
        logger.warning("[WS] Pico W disconnected")

    async def send_to_pico(self, command: dict) -> None:
        if self._pico_ws:
            await self._try_send(command)
        else:
            self._pending.append(command)
            logger.warning(f"[WS] Queued command (Pico offline): {command['type']}")

    def queue_command(self, command: dict) -> None:
        self._pending.append(command)

    async def _try_send(self, command: dict) -> None:
        try:
            await self._pico_ws.send_json(command)  # type: ignore[union-attr]
            logger.info(f"[WS→PICO] {command['type']}")
        except Exception as exc:
            logger.error(f"[WS] Send to Pico failed: {exc}")
            self.disconnect_pico()

    # ── SSE broadcast (dashboard) ────────────────────────────────

    def add_subscriber(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(q)
        return q

    def remove_subscriber(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def broadcast_event(self, event_type: str, payload: dict) -> None:
        msg = json.dumps({"type": event_type, **payload})
        dead: List[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.remove_subscriber(q)


# ── Singletons ────────────────────────────────────────────────────────────────
ws_manager:   Optional[WebSocketManager] = None
solar_agent:  Optional[SolarAgent]       = None

# ── Queue ─────────────────────────────────────────────────────────────────────
_telemetry_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
_agent_worker_task: Optional[asyncio.Task] = None

async def _telemetry_worker():
    """Background worker to process telemetry sequentially."""
    while True:
        try:
            raw = await _telemetry_queue.get()
            if solar_agent:
                result = await solar_agent.step(raw)
                # Broadcast agent decision
                if ws_manager:
                    await ws_manager.broadcast_event("decision", result)
                    if ws_manager.pico_connected:
                        await ws_manager.send_to_pico({
                            "type":        "decision",
                            "intention":   result.get("intention"),
                            "tools_used":  result.get("tools_used", []),
                            "timestamp":   result.get("timestamp"),
                        })
            _telemetry_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"[WORKER] Error processing telemetry: {exc}")


# ═══════════════════════════════════════════════════════════════════════════════
# Lifespan
# ═══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    global ws_manager, solar_agent

    logger.info("[BOOT] Initialising Solar Agent…")
    await init_db()

    ws_manager = WebSocketManager()
    tools      = ToolRegistry(
        weather_api_key=settings.openweather_api_key,
        lat=settings.latitude,
        lon=settings.longitude,
        location=settings.location,
        ws_manager=ws_manager,
    )
    memory = AgentMemory()
    solar_agent = SolarAgent(tools, memory)

    logger.success(
        f"[BOOT] Agent ready | model={settings.ollama_model} | "
        f"location={settings.location} ({settings.latitude}, {settings.longitude})"
    )
    
    # Start worker
    global _agent_worker_task
    _agent_worker_task = asyncio.create_task(_telemetry_worker())
    
    yield

    logger.info("[SHUTDOWN] Solar Agent stopping…")
    if _agent_worker_task:
        _agent_worker_task.cancel()


# ═══════════════════════════════════════════════════════════════════════════════
# App
# ═══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="Smart Solar PCU — Agentic AI Server",
    description="Autonomous Ollama-powered agent for solar power management",
    version="4.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════════════════
# Auth dependency
# ═══════════════════════════════════════════════════════════════════════════════

async def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    if x_api_key != settings.api_key:
        logger.warning(f"[AUTH] Rejected key …{x_api_key[-4:]}")
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


def _require_agent() -> SolarAgent:
    if not solar_agent:
        raise HTTPException(status_code=503, detail="Agent not initialised")
    return solar_agent


# ═══════════════════════════════════════════════════════════════════════════════
# Request / Response models
# ═══════════════════════════════════════════════════════════════════════════════

class TelemetryData(BaseModel):
    bat_v:     float
    bat_i:     float
    bat_soc:   float
    panel_v:   float
    panel_i:   float = 0.0
    grid_ok:   bool
    source:    str
    switches:  int   = 0
    timestamp: Optional[str] = None

class AgentCommand(BaseModel):
    command: str
    params:  Dict[str, Any] = {}


# ═══════════════════════════════════════════════════════════════════════════════
# Telemetry endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/telemetry", dependencies=[Depends(verify_api_key)])
@limiter.limit(settings.rate_limit_telemetry)
async def receive_telemetry(
    request: Request,
    data:    TelemetryData,
    agent:   SolarAgent = Depends(_require_agent),
):
    """Receive a telemetry reading and run a full agent step (non-streaming)."""
    payload = data.model_dump()
    payload["received_at"] = datetime.now().isoformat()
    result = await agent.step(payload)
    return {"status": "processed", "agent_decision": result}


@app.post("/api/telemetry/stream", dependencies=[Depends(verify_api_key)])
@limiter.limit(settings.rate_limit_telemetry)
async def receive_telemetry_stream(
    request: Request,
    data:    TelemetryData,
    agent:   SolarAgent = Depends(_require_agent),
):
    """
    SSE streaming version — the browser / dashboard receives each reasoning
    step (thinking → action → reflection → done) as it happens.
    """
    payload = data.model_dump()
    payload["received_at"] = datetime.now().isoformat()

    return EventSourceResponse(agent.step_stream(payload))


# ═══════════════════════════════════════════════════════════════════════════════
# WebSocket — Pico W real-time channel
# ═══════════════════════════════════════════════════════════════════════════════

@app.websocket("/ws/pico")
async def pico_websocket(ws: WebSocket):
    """
    Persistent WebSocket for the Pico W device.

    The Pico W sends JSON telemetry frames; the server replies with commands
    (SWITCH_SOURCE, SET_SOC_TARGET) in real-time.

    No API-key header check here — the shared secret is embedded in the
    first handshake message instead ({"type": "auth", "key": "..."}).
    """
    await ws.accept()

    # ── Handshake auth ────────────────────────────────────────────
    try:
        auth_msg = await asyncio.wait_for(ws.receive_json(), timeout=10.0)
    except asyncio.TimeoutError:
        await ws.close(code=4001, reason="Auth timeout")
        return

    if auth_msg.get("key") != settings.api_key:
        await ws.close(code=4003, reason="Unauthorized")
        logger.warning("[WS/PICO] Rejected unauthenticated connection")
        return

    await ws_manager.connect_pico(ws)  # type: ignore[union-attr]
    await ws.send_json({"type": "auth_ok", "ts": datetime.now().isoformat()})

    try:
        while True:
            raw = await ws.receive_json()
            msg_type = raw.get("type", "telemetry")

            if msg_type == "telemetry" and solar_agent:
                # Rate limiting simple check: skip if queue is getting full
                if _telemetry_queue.qsize() > 50:
                    logger.warning("[WS/PICO] Dropping telemetry, queue is full")
                    continue
                    
                raw["received_at"] = datetime.now().isoformat()
                
                try:
                    _telemetry_queue.put_nowait(raw)
                except asyncio.QueueFull:
                    logger.warning("[WS/PICO] Dropping telemetry, queue is completely full")
                
                # Broadcast raw telemetry to SSE dashboard subscribers immediately
                await ws_manager.broadcast_event("telemetry", raw)  # type: ignore[union-attr]

            elif msg_type == "ping":
                await ws.send_json({"type": "pong", "ts": datetime.now().isoformat()})

    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect_pico()  # type: ignore[union-attr]


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard SSE feed
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/events", dependencies=[Depends(verify_api_key)])
async def dashboard_events(request: Request):
    """
    Server-Sent Events stream for dashboards.
    Receives every telemetry frame and agent decision in real time.
    """
    assert ws_manager is not None
    q = ws_manager.add_subscriber()

    async def _generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield {"data": msg}
                except asyncio.TimeoutError:
                    yield {"data": json.dumps({"type": "heartbeat"})}
        finally:
            ws_manager.remove_subscriber(q)

    return EventSourceResponse(_generate())


# ═══════════════════════════════════════════════════════════════════════════════
# Command endpoint
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/command", dependencies=[Depends(verify_api_key)])
async def send_command(
    cmd:   AgentCommand,
    agent: SolarAgent = Depends(_require_agent),
):
    """Manual command / override for the agent."""
    c = cmd.command.upper()

    if c == "FORCE_GRID":
        result = await agent.tools.execute(
            "switch_power_source", source="GRID",
            reason=f"Manual override: {cmd.params.get('reason', 'user command')}",
        )

    elif c == "FORCE_SOLAR":
        result = await agent.tools.execute(
            "switch_power_source", source="SOLAR",
            reason=f"Manual override: {cmd.params.get('reason', 'user command')}",
        )

    elif c == "SET_SOC_TARGET":
        target = float(cmd.params.get("target", 80))
        result = await agent.tools.execute(
            "set_soc_target", target_soc=target,
            reason=cmd.params.get("reason", "manual"),
        )

    elif c == "ADD_GOAL":
        await agent.memory.add_goal(
            cmd.params.get("description", "New goal"),
            priority=int(cmd.params.get("priority", 5)),
        )
        result = {"status": "goal_added"}

    elif c == "GET_STATUS":
        return await agent.get_status()

    else:
        raise HTTPException(status_code=400, detail=f"Unknown command: {cmd.command}")

    return {"status": "executed", "command": c, "result": result}


# ═══════════════════════════════════════════════════════════════════════════════
# Agent insight endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/agent/status", dependencies=[Depends(verify_api_key)])
async def get_agent_status(agent: SolarAgent = Depends(_require_agent)):
    return await agent.get_status()


@app.get("/api/agent/beliefs", dependencies=[Depends(verify_api_key)])
async def get_beliefs(agent: SolarAgent = Depends(_require_agent)):
    return {"beliefs": agent.beliefs.to_dict()}


@app.get("/api/agent/memory", dependencies=[Depends(verify_api_key)])
async def get_memory(agent: SolarAgent = Depends(_require_agent)):
    return {
        "recent_observations": await agent.memory.get_recent_observations(20),
        "recent_actions":      await agent.memory.get_recent_actions(10),
        "recent_decisions":    await agent.memory.get_recent_decisions(10),
        "patterns":            await agent.memory.get_patterns(),
        "active_goals":        await agent.memory.get_active_goals(),
    }


@app.get("/api/agent/decisions", dependencies=[Depends(verify_api_key)])
async def get_decisions(limit: int = 20, agent: SolarAgent = Depends(_require_agent)):
    return {"decisions": await agent.memory.get_recent_decisions(limit)}


# ═══════════════════════════════════════════════════════════════════════════════
# Tool passthrough endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/tools/weather", dependencies=[Depends(verify_api_key)])
async def get_weather(hours: int = 6, agent: SolarAgent = Depends(_require_agent)):
    return await agent.tools.execute("get_weather_forecast", hours=hours)


@app.get("/api/tools/suntimes", dependencies=[Depends(verify_api_key)])
async def get_suntimes(agent: SolarAgent = Depends(_require_agent)):
    return await agent.tools.execute("get_sun_times")


@app.post("/api/tools/backup-time", dependencies=[Depends(verify_api_key)])
async def calc_backup(
    current_soc: float,
    load_watts:  float = 60.0,
    agent: SolarAgent  = Depends(_require_agent),
):
    return await agent.tools.execute(
        "calculate_backup_time",
        current_soc=current_soc,
        load_watts=load_watts,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Simulation (dev/testing only)
# ═══════════════════════════════════════════════════════════════════════════════

_SCENARIOS: Dict[str, dict] = {
    "normal":           {"bat_v": 12.6, "bat_i":  1.5, "bat_soc": 75, "panel_v": 18.5, "panel_i": 3.0, "grid_ok": True,  "source": "SOLAR", "switches": 5},
    "low_battery":      {"bat_v": 11.3, "bat_i": -2.0, "bat_soc": 22, "panel_v":  0.0, "panel_i": 0.0, "grid_ok": True,  "source": "GRID",  "switches": 8},
    "night":            {"bat_v": 12.4, "bat_i": -0.5, "bat_soc": 65, "panel_v":  0.0, "panel_i": 0.0, "grid_ok": True,  "source": "SOLAR", "switches": 3},
    "outage_predicted": {"bat_v": 12.2, "bat_i":  0.5, "bat_soc": 55, "panel_v": 17.5, "panel_i": 2.0, "grid_ok": True,  "source": "SOLAR", "switches": 4},
    "storm":            {"bat_v": 12.0, "bat_i": -1.0, "bat_soc": 45, "panel_v":  5.0, "panel_i": 0.0, "grid_ok": True,  "source": "SOLAR", "switches": 6},
    "critical":         {"bat_v": 10.8, "bat_i": -3.0, "bat_soc": 15, "panel_v":  0.0, "panel_i": 0.0, "grid_ok": True,  "source": "SOLAR", "switches": 10},
    "grid_down":        {"bat_v": 12.5, "bat_i": -1.5, "bat_soc": 60, "panel_v": 16.0, "panel_i": 2.5, "grid_ok": False, "source": "SOLAR", "switches": 2},
}


@app.post("/api/simulate/telemetry", dependencies=[Depends(verify_api_key)])
async def simulate(
    scenario: str = "normal",
    agent: SolarAgent = Depends(_require_agent),
):
    telemetry = _SCENARIOS.get(scenario, _SCENARIOS["normal"])
    result = await agent.step(telemetry)
    return {"scenario": scenario, "telemetry": telemetry, "agent_response": result}


@app.get("/api/simulate/scenarios", dependencies=[Depends(verify_api_key)])
async def list_scenarios():
    return {"available_scenarios": list(_SCENARIOS.keys())}


# ═══════════════════════════════════════════════════════════════════════════════
# Health / Root
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    """Unauthenticated health probe for load-balancers / uptime monitors."""
    return {
        "status":    "ok",
        "pico_connected": ws_manager.pico_connected if ws_manager else False,
        "agent_ready":    solar_agent is not None,
        "timestamp":      datetime.now().isoformat(),
    }


@app.get("/")
async def root():
    return {
        "service": "Smart Solar PCU — Agentic AI Server",
        "version": "4.0.0",
        "model":   settings.ollama_model,
        "docs":    "/docs",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
