"""
agent/memory.py — Persistent agent memory backed by SQLite (via aiosqlite).

All operations are fully async.  The memory provides:
  - Short-term:  recent observations and actions (rolling window)
  - Long-term:   decisions + reflections, learned patterns, goals
  - Context:     a pre-formatted string suitable for injection into LLM prompts
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiosqlite

from config import settings


class AgentMemory:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or settings.database_url

    # ── Observations ─────────────────────────────────────────────

    async def add_observation(self, data: dict) -> None:
        async with aiosqlite.connect(self.db_path, timeout=10.0) as db:
            await db.execute(
                "INSERT INTO observations (timestamp, data) VALUES (?, ?)",
                (datetime.now().isoformat(), json.dumps(data)),
            )
            # Keep only the last 500 rows to avoid unbounded growth
            await db.execute(
                "DELETE FROM observations WHERE id NOT IN "
                "(SELECT id FROM observations ORDER BY id DESC LIMIT 500)"
            )
            await db.commit()

    async def get_recent_observations(self, limit: int = 20) -> List[Dict]:
        async with aiosqlite.connect(self.db_path, timeout=10.0) as db:
            async with db.execute(
                "SELECT timestamp, data FROM observations ORDER BY id DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [{"timestamp": r[0], **json.loads(r[1])} for r in reversed(rows)]

    # ── Actions ──────────────────────────────────────────────────

    async def add_action(
        self, action_type: str, params: dict, result: dict, success: bool = True
    ) -> None:
        async with aiosqlite.connect(self.db_path, timeout=10.0) as db:
            await db.execute(
                "INSERT INTO actions (timestamp, action_type, params, result, success) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    datetime.now().isoformat(),
                    action_type,
                    json.dumps(params),
                    json.dumps(result),
                    int(success),
                ),
            )
            await db.commit()

    async def get_recent_actions(self, limit: int = 10) -> List[Dict]:
        async with aiosqlite.connect(self.db_path, timeout=10.0) as db:
            async with db.execute(
                "SELECT timestamp, action_type, params, result, success "
                "FROM actions ORDER BY id DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [
            {
                "timestamp": r[0],
                "type": r[1],
                "params": json.loads(r[2]),
                "result": json.loads(r[3]),
                "success": bool(r[4]),
            }
            for r in reversed(rows)
        ]

    # ── Decisions + Reflections ───────────────────────────────────

    async def save_decision(
        self,
        intention: str,
        reason: str,
        actions: list,
        reflection: str = "",
        telemetry: Optional[dict] = None,
    ) -> int:
        async with aiosqlite.connect(self.db_path, timeout=10.0) as db:
            cur = await db.execute(
                "INSERT INTO decisions "
                "(timestamp, intention, reason, actions, reflection, telemetry_snapshot) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    datetime.now().isoformat(),
                    intention,
                    reason,
                    json.dumps(actions),
                    reflection,
                    json.dumps(telemetry or {}),
                ),
            )
            await db.commit()
            return cur.lastrowid  # type: ignore[return-value]

    async def update_reflection(
        self, decision_id: int, reflection: str, score: float = 0.5
    ) -> None:
        async with aiosqlite.connect(self.db_path, timeout=10.0) as db:
            await db.execute(
                "UPDATE decisions SET reflection = ?, reflection_score = ? WHERE id = ?",
                (reflection, score, decision_id),
            )
            await db.commit()

    async def get_recent_decisions(self, limit: int = 10) -> List[Dict]:
        async with aiosqlite.connect(self.db_path, timeout=10.0) as db:
            async with db.execute(
                "SELECT id, timestamp, intention, reason, actions, reflection, reflection_score "
                "FROM decisions ORDER BY id DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [
            {
                "id": r[0],
                "timestamp": r[1],
                "intention": r[2],
                "reason": r[3],
                "actions": json.loads(r[4]),
                "reflection": r[5],
                "score": r[6],
            }
            for r in reversed(rows)
        ]

    # ── Patterns ─────────────────────────────────────────────────

    async def add_pattern(
        self, name: str, description: str, confidence: float = 0.5
    ) -> None:
        """Upsert a learned pattern, blending confidence with running average."""
        async with aiosqlite.connect(self.db_path, timeout=10.0) as db:
            async with db.execute(
                "SELECT id, occurrences, confidence FROM patterns WHERE name = ?", (name,)
            ) as cur:
                existing = await cur.fetchone()

            if existing:
                row_id, occ, old_conf = existing
                new_conf = min(0.97, (old_conf * occ + confidence) / (occ + 1))
                await db.execute(
                    "UPDATE patterns SET occurrences=?, confidence=?, last_seen=?, description=? "
                    "WHERE id=?",
                    (occ + 1, new_conf, datetime.now().isoformat(), description, row_id),
                )
            else:
                await db.execute(
                    "INSERT INTO patterns (name, description, confidence, occurrences, last_seen) "
                    "VALUES (?, ?, ?, 1, ?)",
                    (name, description, confidence, datetime.now().isoformat()),
                )
            await db.commit()

    async def get_patterns(self, min_confidence: float = 0.3) -> List[Dict]:
        async with aiosqlite.connect(self.db_path, timeout=10.0) as db:
            async with db.execute(
                "SELECT name, description, confidence, occurrences FROM patterns "
                "WHERE confidence >= ? ORDER BY confidence DESC LIMIT 10",
                (min_confidence,),
            ) as cur:
                rows = await cur.fetchall()
        return [
            {"name": r[0], "description": r[1], "confidence": r[2], "occurrences": r[3]}
            for r in rows
        ]

    # ── Goals ────────────────────────────────────────────────────

    async def add_goal(self, description: str, priority: int = 5) -> None:
        async with aiosqlite.connect(self.db_path, timeout=10.0) as db:
            await db.execute(
                "INSERT INTO goals (description, priority, active, created_at) "
                "VALUES (?, ?, 1, ?)",
                (description, priority, datetime.now().isoformat()),
            )
            await db.commit()

    async def get_active_goals(self) -> List[Dict]:
        async with aiosqlite.connect(self.db_path, timeout=10.0) as db:
            async with db.execute(
                "SELECT description, priority FROM goals WHERE active=1 ORDER BY priority DESC"
            ) as cur:
                rows = await cur.fetchall()
        return [{"description": r[0], "priority": r[1]} for r in rows]

    # ── Power Stats ──────────────────────────────────────────────

    async def update_daily_power(self, power_w: float) -> None:
        """Keep track of the peak power seen today."""
        if power_w <= 0:
            return
            
        today = datetime.now().strftime("%Y-%m-%d")
        async with aiosqlite.connect(self.db_path, timeout=10.0) as db:
            # Check current max
            async with db.execute("SELECT max_power_w FROM daily_power_stats WHERE date = ?", (today,)) as cur:
                row = await cur.fetchone()
                
            if not row:
                await db.execute("INSERT INTO daily_power_stats(date, max_power_w) VALUES (?, ?)", (today, power_w))
            elif power_w > row[0]:
                await db.execute("UPDATE daily_power_stats SET max_power_w = ? WHERE date = ?", (power_w, today))
                
            await db.commit()

    async def get_panel_efficiency(self) -> Dict[str, Any]:
        """Compute the efficiency drop from all-time peak to recent peaks."""
        async with aiosqlite.connect(self.db_path, timeout=10.0) as db:
            async with db.execute("SELECT MAX(max_power_w) FROM daily_power_stats") as cur:
                all_time_row = await cur.fetchone()
                all_time_peak = all_time_row[0] if all_time_row and all_time_row[0] else 0.0

            # Get average peak from the last 3 days
            async with db.execute(
                "SELECT AVG(max_power_w) FROM (SELECT max_power_w FROM daily_power_stats ORDER BY date DESC LIMIT 3)"
            ) as cur:
                recent_row = await cur.fetchone()
                recent_peak = recent_row[0] if recent_row and recent_row[0] else 0.0

        if all_time_peak == 0.0:
            return {"all_time_peak": 0.0, "recent_peak": 0.0, "drop_pct": 0.0}

        drop_pct = max(0.0, ((all_time_peak - recent_peak) / all_time_peak) * 100)
        return {
            "all_time_peak": round(all_time_peak, 1),
            "recent_peak": round(recent_peak, 1),
            "drop_pct": round(drop_pct, 1),
        }

    # ── Context builder (for LLM injection) ──────────────────────

    async def get_context_summary(self) -> str:
        """
        Builds a compact context block that is injected into every LLM prompt.
        Keeps token count low while giving the agent enough history to reason well.
        """
        actions  = await self.get_recent_actions(5)
        patterns = await self.get_patterns(0.5)
        goals    = await self.get_active_goals()
        eff      = await self.get_panel_efficiency()

        actions_str = "\n".join(
            f"  [{a['timestamp'][:16]}] {a['type']}({_fmt(a['params'])}) → {_fmt(a['result'])}"
            for a in actions
        ) or "  (none yet)"

        patterns_str = "\n".join(
            f"  [{p['confidence']:.0%}] {p['name']}: {p['description']}"
            for p in patterns
        ) or "  (no patterns learned yet)"

        goals_str = "\n".join(
            f"  [P{g['priority']}] {g['description']}"
            for g in goals
        )
        
        health_str = f"  All-time peak: {eff['all_time_peak']}W, Recent peak avg: {eff['recent_peak']}W -> {eff['drop_pct']}% efficiency drop"

        return (
            f"PANEL HEALTH:\n{health_str}\n\n"
            f"ACTIVE GOALS:\n{goals_str}\n\n"
            f"RECENT ACTIONS:\n{actions_str}\n\n"
            f"LEARNED PATTERNS:\n{patterns_str}"
        )


def _fmt(obj: Any, max_len: int = 80) -> str:
    """Compact repr of a dict for inline display."""
    s = json.dumps(obj, separators=(",", ":"))
    return s if len(s) <= max_len else s[:max_len] + "…"
