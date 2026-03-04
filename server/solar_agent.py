"""
agent/solar_agent.py — The autonomous agent.

The loop: OBSERVE → REASON+PLAN+ACT (Ollama tool-calling) → REFLECT

Key design decisions:
  - Ollama's AsyncClient is used throughout so the event-loop is never blocked.
  - The tool-calling loop runs up to MAX_TOOL_ROUNDS iterations per step; Ollama
    drives the loop by returning tool_calls until it decides it is done.
  - Reflection is fire-and-forget (asyncio.create_task) so it never delays the
    telemetry response.
  - step_stream() is an async generator that yields SSE-compatible JSON strings
    for the /api/telemetry/stream endpoint.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

import ollama
from loguru import logger

from .beliefs import BeliefState
from .memory import AgentMemory
from .prompts import REFLECTION_PROMPT, SOLAR_AGENT_SYSTEM
from .tools import ToolRegistry
from config import settings

MAX_TOOL_ROUNDS = 6  # Safety ceiling on iterative tool-calling depth


class SolarAgent:
    def __init__(self, tools: ToolRegistry, memory: AgentMemory) -> None:
        self.tools  = tools
        self.memory = memory
        self.beliefs = BeliefState()
        self._client = ollama.AsyncClient(host=settings.ollama_host)
        self.model   = settings.ollama_model

        self._system = SOLAR_AGENT_SYSTEM.format(
            location=settings.location,
            lat=settings.latitude,
            lon=settings.longitude,
        )

        # IDs used for deferred reflection
        self._last_decision_id: Optional[int] = None
        self._last_telemetry:   Optional[dict] = None
        self._reflection_semaphore = asyncio.Semaphore(2)

    # ── Public API ────────────────────────────────────────────────

    async def step(self, telemetry: dict) -> Dict[str, Any]:
        """
        Full OBSERVE → REASON → PLAN → ACT → REFLECT cycle (non-streaming).
        Safe: any internal error returns a safe-mode dict instead of raising.
        """
        try:
            await self._observe(telemetry)
            result = await self._reason_and_act(telemetry)

            # Deferred reflection on the *previous* decision
            if self._last_decision_id and self._last_telemetry:
                if not self._reflection_semaphore.locked():
                    asyncio.create_task(
                        self._reflect(
                            self._last_decision_id,
                            self._last_telemetry,
                            telemetry,
                        )
                    )
                else:
                    logger.warning("[AGENT] Skipping reflection: max queued")

            self._last_decision_id = result["decision_id"]
            self._last_telemetry   = telemetry
            return result

        except Exception as exc:
            logger.exception("[AGENT] step() failed")
            return {
                "error":        str(exc),
                "intention":    "SAFE_MODE",
                "reason":       "Internal agent error — no action taken this cycle.",
                "actions_taken": 0,
            }

    async def step_stream(self, telemetry: dict) -> AsyncGenerator[str, None]:
        """
        SSE streaming version: yields JSON-encoded event strings so the caller
        can forward them to a browser/dashboard with EventSourceResponse.
        """
        try:
            await self._observe(telemetry)
            context = await self.memory.get_context_summary()

            yield _sse("thinking", {"content": "Observing telemetry…"})

            messages: List[Dict] = [
                {"role": "user", "content": self._user_message(telemetry, context)}
            ]
            tool_calls_made: List[Dict] = []
            final_content = ""

            for _round in range(MAX_TOOL_ROUNDS):
                response = await self._client.chat(
                    model=self.model,
                    messages=[{"role": "system", "content": self._system}] + messages,
                    tools=self.tools.schemas,
                    stream=False,
                )
                msg = response["message"]
                final_content = msg.get("content") or ""

                if final_content:
                    yield _sse("reasoning", {"content": final_content})

                tool_calls: List[Dict] = msg.get("tool_calls") or []
                if not tool_calls:
                    break  # LLM is satisfied

                tool_results: List[Dict] = []
                for tc in tool_calls:
                    fn_name = tc["function"]["name"]
                    fn_args = _parse_args(tc["function"]["arguments"])

                    yield _sse("action", {"tool": fn_name, "args": fn_args})

                    result = await self.tools.execute(fn_name, **fn_args)
                    await self.memory.add_action(fn_name, fn_args, result)
                    tool_calls_made.append({"tool": fn_name, "args": fn_args, "result": result})

                    tool_results.append({"role": "tool", "content": json.dumps(result)})

                messages.append({
                    "role": "assistant",
                    "content": final_content,
                    "tool_calls": tool_calls,
                })
                messages.extend(tool_results)

            decision_id = await self.memory.save_decision(
                intention=_extract_intention(final_content),
                reason=final_content[:600],
                actions=tool_calls_made,
                telemetry=telemetry,
            )

            # Deferred reflection
            if self._last_decision_id and self._last_telemetry:
                if not self._reflection_semaphore.locked():
                    asyncio.create_task(
                        self._reflect(self._last_decision_id, self._last_telemetry, telemetry)
                    )
                else:
                    logger.warning("[AGENT] Skipping reflection: max queued")

            self._last_decision_id = decision_id
            self._last_telemetry   = telemetry

            yield _sse("done", {
                "decision_id":   decision_id,
                "actions_taken": len(tool_calls_made),
                "tools_used":    [t["tool"] for t in tool_calls_made],
            })

        except Exception as exc:
            logger.exception("[AGENT] step_stream() failed")
            yield _sse("error", {"message": str(exc)})

    async def get_status(self) -> Dict[str, Any]:
        return {
            "model":            self.model,
            "ollama_host":      settings.ollama_host,
            "beliefs":          self.beliefs.to_dict(),
            "recent_decisions": await self.memory.get_recent_decisions(5),
            "learned_patterns": await self.memory.get_patterns(),
            "active_goals":     await self.memory.get_active_goals(),
        }

    # ── Private helpers ───────────────────────────────────────────

    async def _observe(self, telemetry: dict) -> None:
        """OBSERVE: ingest telemetry into beliefs + persistent memory."""
        self.beliefs.update_from_telemetry(telemetry)
        await self.memory.add_observation(telemetry)
        
        # Track daily peak power
        if "panel_v" in telemetry and "panel_i" in telemetry:
            power = telemetry["panel_v"] * telemetry["panel_i"]
            await self.memory.update_daily_power(power)

    async def _reason_and_act(self, telemetry: dict) -> Dict[str, Any]:
        """REASON + PLAN + ACT: non-streaming Ollama tool-calling loop."""
        context = await self.memory.get_context_summary()
        messages: List[Dict] = [
            {"role": "user", "content": self._user_message(telemetry, context)}
        ]
        tool_calls_made: List[Dict] = []
        final_content = ""

        for _round in range(MAX_TOOL_ROUNDS):
            response = await self._client.chat(
                model=self.model,
                messages=[{"role": "system", "content": self._system}] + messages,
                tools=self.tools.schemas,
                stream=False,
            )
            msg = response["message"]
            final_content = msg.get("content") or final_content or ""

            tool_calls: List[Dict] = msg.get("tool_calls") or []
            if not tool_calls:
                break

            tool_results: List[Dict] = []
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = _parse_args(tc["function"]["arguments"])

                logger.info(f"[AGENT] → {fn_name}({fn_args})")
                result = await self.tools.execute(fn_name, **fn_args)
                await self.memory.add_action(fn_name, fn_args, result)
                tool_calls_made.append({"tool": fn_name, "args": fn_args, "result": result})

                tool_results.append({"role": "tool", "content": json.dumps(result)})

            messages.append({
                "role": "assistant",
                "content": final_content,
                "tool_calls": tool_calls,
            })
            messages.extend(tool_results)

        if not final_content:
            final_content = "No explicit reasoning provided by the model."
            
        intention   = _extract_intention(final_content)
        decision_id = await self.memory.save_decision(
            intention=intention,
            reason=final_content[:600],
            actions=tool_calls_made,
            telemetry=telemetry,
        )

        logger.info(f"[AGENT] Decision {decision_id}: {intention} | tools={len(tool_calls_made)}")
        return {
            "intention":     intention,
            "reason":        final_content,
            "actions_taken": len(tool_calls_made),
            "tools_used":    [t["tool"] for t in tool_calls_made],
            "decision_id":   decision_id,
            "timestamp":     datetime.now().isoformat(),
        }

    async def _reflect(
        self, decision_id: int, old_telemetry: dict, new_telemetry: dict
    ) -> None:
        """
        REFLECT: ask the LLM to score a past decision given new evidence.
        Runs as a background task — never blocks the main agent loop.
        Bounded by self._reflection_semaphore.
        """
        async with self._reflection_semaphore:
            try:
                prompt = REFLECTION_PROMPT.format(
                decision=f"decision_id={decision_id}",
                telemetry=json.dumps(old_telemetry, indent=2),
                elapsed_minutes=5,
                current_telemetry=json.dumps(new_telemetry, indent=2),
            )
            response = await self._client.chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                stream=False,
            )
            content = response["message"].get("content", "")

            # Extract JSON block from the response
            start, end = content.find("{"), content.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(content[start:end])
                score   = float(data.get("score", 0.5))
                lesson  = str(data.get("lesson", ""))
                pattern = str(data.get("pattern", ""))

                await self.memory.update_reflection(decision_id, lesson, score)

                if pattern:
                    name = f"auto_{decision_id}"
                    await self.memory.add_pattern(name, pattern, confidence=score)

                logger.info(
                    f"[REFLECT] decision={decision_id} score={score:.2f} lesson='{lesson[:80]}'"
                )
        except Exception:
            logger.exception(f"[REFLECT] Background reflection failed for decision {decision_id}")

    def _user_message(self, telemetry: dict, context: str) -> str:
        return (
            f"## Current System State\n{self.beliefs.to_summary()}\n\n"
            f"## Raw Telemetry\n"
            f"  battery : {telemetry.get('bat_soc',0):.1f}% SOC | "
            f"{telemetry.get('bat_v',0):.2f}V | {telemetry.get('bat_i',0):+.2f}A\n"
            f"  panel   : {telemetry.get('panel_v',0):.1f}V | {telemetry.get('panel_i',0):.2f}A\n"
            f"  grid_ok : {telemetry.get('grid_ok',False)} | source: {telemetry.get('source','?')}\n"
            f"  switches_today: {telemetry.get('switches',0)}\n"
            f"  time    : {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"## Agent Context\n{context}\n\n"
            "## Instruction\n"
            "Analyse the current state, call any tools you need, "
            "then produce a concise summary of your decision and expected outcome."
        )


# ── Module-level helpers ──────────────────────────────────────────────────────

def _parse_args(raw: Any) -> dict:
    """Ollama sometimes returns args as a string; normalise to dict."""
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _extract_intention(text: str) -> str:
    tl = text.lower()
    if "switch to grid"   in tl or "switching to grid"   in tl: return "SWITCH_TO_GRID"
    if "switch to solar"  in tl or "switching to solar"  in tl: return "SWITCH_TO_SOLAR"
    if "critical"         in tl and "battery" in tl:             return "CRITICAL_ALERT"
    if "no action"        in tl or "maintain current"    in tl:  return "MAINTAIN_CURRENT"
    if "pre-charge"       in tl or "charge via grid"     in tl:  return "PRE_CHARGE"
    if "charge"           in tl:                                  return "OPTIMIZE_CHARGING"
    return "MONITOR"


def _sse(event_type: str, payload: dict) -> str:
    """Format a single SSE line."""
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"
