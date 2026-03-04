"""
agent/beliefs.py — The agent's world model.

A BeliefState is updated from raw telemetry and enriched with derived flags
that the LLM can reason over without having to re-derive them itself.
"""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class BeliefState:
    # ── Battery ──────────────────────────────────────────────────
    battery_soc: float = 50.0        # State of Charge %
    battery_voltage: float = 12.0    # Volts
    battery_current: float = 0.0     # Amps (+ve = charging)
    battery_trend: str = "stable"    # "charging" | "discharging" | "stable"
    battery_critical: bool = False   # SOC < 20 %
    battery_low: bool = False        # SOC < 35 %
    battery_full: bool = False       # SOC > 95 %

    # ── Solar panel ───────────────────────────────────────────────
    panel_voltage: float = 0.0
    panel_current: float = 0.0
    panel_power: float = 0.0
    can_charge_from_solar: bool = False  # panel_v > 13 V

    # ── Grid / source ─────────────────────────────────────────────
    grid_available: bool = True
    current_source: str = "GRID"

    # ── Environment (enriched by tools) ──────────────────────────
    is_dark: bool = False
    hours_to_sunset: float = 6.0
    hours_to_sunrise: float = 12.0
    weather_cloudy: bool = False
    weather_description: str = "unknown"
    temperature: float = 25.0
    solar_potential: str = "unknown"  # "high" | "medium" | "low"

    # ── Predictions ───────────────────────────────────────────────
    predicted_outage_risk: float = 0.0   # 0–1
    backup_time_hours: float = 4.0
    estimated_solar_hours: float = 0.0

    # ── Operational context ───────────────────────────────────────
    switches_today: int = 0
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())

    # ─────────────────────────────────────────────────────────────

    def update_from_telemetry(self, t: dict) -> None:
        """Ingest raw Pico W telemetry and recompute all derived flags."""
        self.battery_soc     = float(t.get("bat_soc", self.battery_soc))
        self.battery_voltage = float(t.get("bat_v",   self.battery_voltage))
        self.battery_current = float(t.get("bat_i",   self.battery_current))
        self.panel_voltage   = float(t.get("panel_v", self.panel_voltage))
        self.panel_current   = float(t.get("panel_i", self.panel_current))
        self.panel_power     = self.panel_voltage * self.panel_current
        self.grid_available  = bool(t.get("grid_ok",  self.grid_available))
        self.current_source  = str(t.get("source",    self.current_source))
        self.switches_today  = int(t.get("switches",  self.switches_today))
        self.last_updated    = datetime.now().isoformat()

        # Derived flags
        self.battery_critical      = self.battery_soc < 20
        self.battery_low           = self.battery_soc < 35
        self.battery_full          = self.battery_soc > 95
        self.can_charge_from_solar = self.panel_voltage > 13.0

        # Trend
        if self.battery_current > 0.5:
            self.battery_trend = "charging"
        elif self.battery_current < -0.5:
            self.battery_trend = "discharging"
        else:
            self.battery_trend = "stable"

    def to_summary(self) -> str:
        """Compact human-readable state for LLM context."""
        flags = []
        if self.battery_critical: flags.append("⚠ CRITICAL")
        if self.battery_low:      flags.append("⚠ LOW")
        if self.battery_full:     flags.append("✓ FULL")
        if self.is_dark:          flags.append("🌙 DARK")
        if self.weather_cloudy:   flags.append("☁ CLOUDY")

        return (
            f"BATTERY : {self.battery_soc:.1f}% | {self.battery_voltage:.2f}V | "
            f"{self.battery_current:+.2f}A | trend={self.battery_trend} "
            f"| backup≈{self.backup_time_hours:.1f}h  {' '.join(flags)}\n"
            f"SOLAR   : {self.panel_voltage:.1f}V / {self.panel_power:.1f}W "
            f"| can_charge={self.can_charge_from_solar} | potential={self.solar_potential}\n"
            f"GRID    : available={self.grid_available} | source={self.current_source}\n"
            f"SUN     : dark={self.is_dark} | sunset_in={self.hours_to_sunset:.1f}h "
            f"| sunrise_in={self.hours_to_sunrise:.1f}h\n"
            f"WEATHER : {self.weather_description} | temp={self.temperature:.0f}°C\n"
            f"RISK    : outage_risk={self.predicted_outage_risk:.0%} | switches_today={self.switches_today}"
        )

    def to_dict(self) -> dict:
        return self.__dict__.copy()
