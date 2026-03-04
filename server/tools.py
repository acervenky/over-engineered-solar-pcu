"""
agent/tools.py — Every tool the agent can call, plus their Ollama/OpenAI schemas.

Tools are plain async functions wrapped in a registry.  Each tool:
  - has a validated input signature
  - returns a dict (never raises — errors are returned as {"error": "..."})
  - is fully async so it can be awaited inside the agent loop

Hardware commands (switch_power_source, set_soc_target) are delivered to the
Pico W via a WebSocketManager injected at startup.  If no WS is connected the
command is queued instead.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

import httpx
from astral import LocationInfo
from astral.sun import sun
from loguru import logger


# ── Registry ─────────────────────────────────────────────────────────────────

class ToolRegistry:
    def __init__(
        self,
        weather_api_key: str,
        lat: float,
        lon: float,
        location: str,
        ws_manager=None,
    ) -> None:
        self.weather_api_key = weather_api_key
        self.lat = lat
        self.lon = lon
        self.location = location
        self.ws_manager = ws_manager  # injected after WS manager is created

        self._tools: Dict[str, Callable] = {
            "switch_power_source":  self._switch_power_source,
            "get_weather_forecast":  self._get_weather_forecast,
            "get_sun_times":         self._get_sun_times,
            "calculate_backup_time": self._calculate_backup_time,
            "set_soc_target":        self._set_soc_target,
            "send_alert":            self._send_alert,
        }

    async def execute(self, name: str, **kwargs) -> Dict[str, Any]:
        if name not in self._tools:
            return {"error": f"Unknown tool: {name}"}
        try:
            result = await self._tools[name](**kwargs)
            logger.debug(f"[TOOL] {name}({kwargs}) → {result}")
            return result
        except Exception as exc:
            logger.error(f"[TOOL] {name} raised {exc!r}")
            return {"error": str(exc), "tool": name}

    # ── Tool implementations ──────────────────────────────────────

    async def _switch_power_source(self, source: str, reason: str) -> Dict:
        """Send a SWITCH_SOURCE command to the Pico W."""
        if source not in ("SOLAR", "GRID"):
            return {"error": f"Invalid source '{source}'. Must be SOLAR or GRID."}

        command = {
            "type": "SWITCH_SOURCE",
            "source": source,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
        }
        if self.ws_manager and self.ws_manager.pico_connected:
            await self.ws_manager.send_to_pico(command)
            status = "sent"
        else:
            # Queue for the next time Pico W polls
            self.ws_manager and self.ws_manager.queue_command(command)
            status = "queued"
            logger.warning("[TOOL] Pico W not connected — command queued")

        return {"status": status, "command": command}

    async def _get_weather_forecast(self, hours: int = 6) -> Dict:
        if not self.weather_api_key:
            return {
                "error": "OPENWEATHER_API_KEY not configured",
                "cloudy": False,
                "solar_potential": "unknown",
                "description": "no weather data",
            }

        cnt = max(1, min(hours // 3, 8))  # OpenWeather 3-hour buckets
        url = "https://api.openweathermap.org/data/2.5/forecast"
        params = {
            "lat": self.lat, "lon": self.lon,
            "appid": self.weather_api_key,
            "units": "metric", "cnt": cnt,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        items = data.get("list", [])
        if not items:
            return {"error": "Empty forecast response"}

        total_clouds = 0
        rain_expected = False
        forecasts: List[Dict] = []

        for item in items:
            clouds = item.get("clouds", {}).get("all", 0)
            total_clouds += clouds
            weather_main = item.get("weather", [{}])[0].get("main", "")
            if weather_main in ("Rain", "Drizzle", "Thunderstorm", "Snow"):
                rain_expected = True
            forecasts.append({
                "time":        item.get("dt_txt", ""),
                "clouds_pct":  clouds,
                "weather":     weather_main,
                "temp_c":      item["main"].get("temp", 25),
                "description": item.get("weather", [{}])[0].get("description", ""),
            })

        avg_clouds = total_clouds / len(items)
        solar_potential = (
            "low"    if avg_clouds > 70 else
            "medium" if avg_clouds > 30 else
            "high"
        )

        return {
            "location":        self.location,
            "avg_cloud_pct":   round(avg_clouds, 1),
            "cloudy":          avg_clouds > 60,
            "rain_expected":   rain_expected,
            "solar_potential": solar_potential,
            "description":     forecasts[0]["description"],
            "next_3_periods":  forecasts[:3],
        }

    async def _get_sun_times(self) -> Dict:
        from config import settings
        city = LocationInfo(
            name=self.location,
            region="Global",
            timezone=settings.timezone,
            latitude=self.lat,
            longitude=self.lon,
        )
        s = sun(city.observer, date=date.today(), tzinfo=city.timezone)
        now = datetime.now(tz=s["sunrise"].tzinfo)

        sunrise, sunset = s["sunrise"], s["sunset"]
        is_dark = now < sunrise or now > sunset

        if now < sunset:
            hours_to_sunset = max(0.0, (sunset - now).total_seconds() / 3600)
        else:
            hours_to_sunset = 0.0

        if now > sunset:
            tomorrow_sunrise = sun(
                city.observer,
                date=date.fromordinal(date.today().toordinal() + 1),
                tzinfo=city.timezone,
            )["sunrise"]
            hours_to_sunrise = max(0.0, (tomorrow_sunrise - now).total_seconds() / 3600)
        else:
            hours_to_sunrise = 0.0

        daylight_hours = (sunset - sunrise).total_seconds() / 3600

        return {
            "sunrise":          sunrise.strftime("%H:%M"),
            "sunset":           sunset.strftime("%H:%M"),
            "current_time":     now.strftime("%H:%M"),
            "is_dark":          is_dark,
            "hours_to_sunset":  round(hours_to_sunset, 2),
            "hours_to_sunrise": round(hours_to_sunrise, 2),
            "daylight_hours":   round(daylight_hours, 1),
        }

    async def _calculate_backup_time(
        self,
        current_soc: float,
        load_watts: float = 60.0,
        battery_ah: float = 100.0,
        battery_voltage: float = 12.0,
    ) -> Dict:
        """
        Estimate hours of backup at given load.
        We treat 20 % SOC as the floor (hardware cut-off).
        """
        usable_soc = max(0.0, current_soc - 20.0)
        usable_wh  = (usable_soc / 100) * battery_ah * battery_voltage
        hours      = usable_wh / load_watts if load_watts > 0 else 0.0

        status = (
            "critical" if hours < 1 else
            "low"      if hours < 2 else
            "adequate" if hours < 4 else
            "good"
        )

        return {
            "current_soc":     current_soc,
            "load_watts":      load_watts,
            "estimated_hours": round(hours, 2),
            "usable_wh":       round(usable_wh, 1),
            "status":          status,
        }

    async def _set_soc_target(self, target_soc: float, reason: str) -> Dict:
        command = {
            "type":      "SET_SOC_TARGET",
            "target":    target_soc,
            "reason":    reason,
            "timestamp": datetime.now().isoformat(),
        }
        if self.ws_manager and self.ws_manager.pico_connected:
            await self.ws_manager.send_to_pico(command)
        else:
            self.ws_manager and self.ws_manager.queue_command(command)

        return {"status": "ok", "target_soc": target_soc, "reason": reason}

    async def _send_alert(self, level: str, message: str) -> Dict:
        ts = datetime.now().isoformat()
        alert = {"level": level, "message": message, "timestamp": ts}

        if level == "critical":
            logger.critical(f"[ALERT] {message}")
        elif level == "warning":
            logger.warning(f"[ALERT] {message}")
        else:
            logger.info(f"[ALERT] {message}")

        # Broadcast alert to all dashboard SSE subscribers if ws_manager available
        if self.ws_manager:
            await self.ws_manager.broadcast_event("alert", alert)

        return {"status": "sent", "alert": alert}

    # ── Ollama / OpenAI tool schemas ──────────────────────────────

    @property
    def schemas(self) -> List[Dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "switch_power_source",
                    "description": (
                        "Switch the PCU between SOLAR (battery-backed inverter) and GRID. "
                        "Use when battery protection, solar optimisation, or a relay rule fires."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source": {
                                "type": "string",
                                "enum": ["SOLAR", "GRID"],
                                "description": "Target source to switch to.",
                            },
                            "reason": {
                                "type": "string",
                                "description": "One-sentence reason for the switch.",
                            },
                        },
                        "required": ["source", "reason"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_weather_forecast",
                    "description": "Fetch an hourly cloud-cover / rain forecast to assess solar potential.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "hours": {
                                "type": "integer",
                                "description": "Forecast horizon in hours (3–24).",
                                "default": 6,
                            }
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_sun_times",
                    "description": "Return today's sunrise / sunset times and hours remaining to each.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "calculate_backup_time",
                    "description": "Estimate how many hours the battery can sustain a given load.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "current_soc": {
                                "type": "number",
                                "description": "Current battery SOC (%).",
                            },
                            "load_watts": {
                                "type": "number",
                                "description": "Estimated load in watts.",
                                "default": 60,
                            },
                        },
                        "required": ["current_soc"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "set_soc_target",
                    "description": "Update the battery SOC target for charge management.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "target_soc": {
                                "type": "number",
                                "description": "New target SOC percentage (0–100).",
                            },
                            "reason": {"type": "string"},
                        },
                        "required": ["target_soc", "reason"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "send_alert",
                    "description": "Emit an alert for critical or warning conditions.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "level": {
                                "type": "string",
                                "enum": ["info", "warning", "critical"],
                            },
                            "message": {"type": "string"},
                        },
                        "required": ["level", "message"],
                    },
                },
            },
        ]
