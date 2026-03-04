# Agentic AI Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AGENT OPERATIONAL LOOP                             │
└─────────────────────────────────────────────────────────────────────────────┘

    ┌──────────┐
    │  START   │
    └────┬─────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────────────────────────────────────────┐
│   1. PERCEIVE   │     │ Receive telemetry from Pico W                       │
│                 │────▶│ - Battery: voltage, current, SOC                    │
│   (Observe)     │     │ - Panel: voltage, power                             │
│                 │     │ - Grid: availability                                │
└────────┬────────┘     └─────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────────────────────────────────────────┐
│ 2. UPDATE       │     │ Enrich with external data                           │
│    BELIEFS      │────▶│ • Weather API → solar forecast                      │
│                 │     │ • Astral → sunrise/sunset                           │
│  (Understand)   │     │ • Calculate → backup time                           │
│                 │     │ • Patterns → outage risk                            │
└────────┬────────┘     └─────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CURRENT BELIEF STATE                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│  Raw Beliefs          │  Derived Beliefs           │  Status Flags          │
│  ─────────────────    │  ──────────────────────    │  ─────────────         │
│  battery_soc: 55%     │  can_charge: True          │  battery_critical: F   │
│  battery_v: 12.2V     │  is_dark: False            │  battery_low: F        │
│  panel_v: 17.5V       │  hours_to_sunset: 1.5      │  grid_available: T     │
│  grid_ok: True        │  predicted_outage: 0.7     │  panel_clean: F        │
│  source: SOLAR        │  backup_time: 3.2h         │                        │
│                       │  weather_cloudy: True      │                        │
│                       │  efficiency_drop: 32%      │                        │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────────────────────────────────────────┐
│   3. REASON     │     │ Evaluate situations and generate intentions         │
│                 │────▶│ • Emergency? (SOC < 20%) → FORCE_GRID               │
│    (Decide)     │     │ • Night + low SOC? → switch_to_grid (priority 8)    │
│                 │     │ • Storm predicted? → switch_to_grid (priority 9)    │
│                 │     │ • Surplus solar? → switch_to_solar (priority 6)     │
└────────┬────────┘     └─────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    INTENTIONS (Prioritized)                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  1. [9] switch_to_grid: "High outage risk (70%), conserving 55% battery"    │
│  2. [8] set_soc_target(85): "Maintain reserve for predicted outage"         │
│  3. [5] maintain_current_state: "No action needed"                          │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────────────────────────────────────────┐
│   4. PLAN       │     │ Create multi-step plan                              │
│                 │────▶│ Goal: "Conserve battery for predicted outage"       │
│   (Strategy)    │     │ Steps:                                              │
│                 │     │   1. switch_power_source(GRID)                      │
│                 │     │   2. set_soc_target(85, "Storm reserve")            │
└────────┬────────┘     └─────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────────────────────────────────────────┐
│    5. ACT       │     │ Execute tools                                       │
│                 │────▶│ ┌─────────────┐    ┌─────────────┐                  │
│   (Execute)     │     │ │  Tool:      │───▶│  Result:    │                  │
│                 │     │ │ switch_power│    │  Success    │                  │
│                 │     │ │ _source     │    │  → GRID     │                  │
│                 │     │ └─────────────┘    └─────────────┘                  │
│                 │     │        │                   │                         │
│                 │     │        ▼                   ▼                         │
│                 │     │ ┌─────────────┐    ┌─────────────┐                  │
│                 │     │ │  Tool:      │───▶│  Result:    │                  │
│                 │     │ │ set_soc     │    │  Success    │                  │
│                 │     │ │ _target     │    │  → 85%      │                  │
│                 │     │ └─────────────┘    └─────────────┘                  │
└────────┬────────┘     └─────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────────────────────────────────────────┐
│  6. REFLECT     │     │ Learn from outcome                                  │
│                 │────▶│ • Record success in memory                          │
│   (Learn)       │     │ • Update patterns: "Storm → conserve battery"       │
│                 │     │ • Mark goal progress                                │
│                 │     │ • If failed: add episode, adjust strategy           │
└────────┬────────┘     └─────────────────────────────────────────────────────┘
         │
         ▼
    ┌──────────┐
    │   WAIT   │◀────────────────────────────────────────────────────────────┐
    │  (1 sec) │                                                     │      │
    └────┬─────┘                                                     │      │
         │                                                            │      │
         └────────────────────────────────────────────────────────────┘      │
                              (Loop back to PERCEIVE) ◀──────────────────────┘


┌─────────────────────────────────────────────────────────────────────────────┐
│                           AGENT MEMORY STRUCTURE                             │
└─────────────────────────────────────────────────────────────────────────────┘

Short-Term Memory (FIFO)
┌─────────────────────────────────────────────────────────────────┐
│ Observations (last 100)          Actions (last 50)              │
│ ───────────────────────          ─────────────────              │
│ • {SOC: 55%, time: 18:30}        • switch_to_grid @ 18:30       │
│ • {SOC: 58%, time: 18:25}        • set_soc_target @ 18:30       │
│ • {SOC: 62%, time: 18:20}        • switch_to_solar @ 14:15      │
│ • ...                            • ...                          │
└─────────────────────────────────────────────────────────────────┘

Long-Term Memory
┌─────────────────────────────────────────────────────────────────┐
│ Episodes (significant events)      Learned Patterns             │
│ ─────────────────────────────      ────────────────             │
│ • Power outage: 2024-03-01 20:15   • outage_times: [20,21,14]   │
│   Duration: 2.5h, Battery used     • avg_drain_rate: 8.5%/h     │
│ • Storm event: 2024-02-28          • avg_charge_rate: 12.3%/h   │
│   Action: Conserved battery        • grid_reliability: 94%      │
│ • Grid failure: 2024-02-15                                     │
│   Action: Emergency switch         Goals                        │
│                                    ─────                        │
│                                    [9] Maintain battery health  │
│                                    [8] Maximize solar use       │
│                                    [7] Predict outages          │
└─────────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────────┐
│                           TOOLS AVAILABLE TO AGENT                           │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────┐
│ switch_power    │  │ set_soc_target  │  │ get_weather     │  │ get_sun     │
│ _source         │  │                 │  │ _forecast       │  │ _times      │
│ ─────────────── │  │ ─────────────── │  │ ─────────────── │  │ ─────────── │
│ Input:          │  │ Input:          │  │ Input:          │  │ Input:      │
│   source:       │  │   target_soc:   │  │   hours: 6      │  │   -         │
│     GRID/SOLAR  │  │   reason:       │  │                 │  │             │
│   reason:       │  │   duration_h:   │  │ Output:         │  │ Output:     │
│     "..."       │  │                 │  │   solar_watts   │  │   sunrise   │
│                 │  │ Output:         │  │   cloud_%       │  │   sunset    │
│ Output:         │  │   new_target    │  │   condition     │  │   hours_to  │
│   success       │  │                 │  │                 │  │   _sunset   │
│   message       │  │                 │  │                 │  │             │
└─────────────────┘  └─────────────────┘  └─────────────────┘  └─────────────┘

┌─────────────────┐  ┌─────────────────┐
│ calculate_backup│  │ send_alert      │
│ _time           │  │                 │
│ ─────────────── │  │ ─────────────── │
│ Input:          │  │ Input:          │
│   current_soc   │  │   level:        │
│   load_watts    │  │     info/       │
│                 │  │     warning/    │
│ Output:         │  │     critical    │
│   backup_hours  │  │   message:      │
│   backup_until  │  │     "..."       │
└─────────────────┘  └─────────────────┘


┌─────────────────────────────────────────────────────────────────────────────┐
│                          DECISION EXAMPLES                                   │
└─────────────────────────────────────────────────────────────────────────────┘

Example 1: Evening Low Battery
──────────────────────────────
Telemetry: SOC 60%, 6:30 PM, Sunset 7:00 PM, Grid OK
Beliefs:  hours_to_sunset=0.5, is_dark=False, predicted_risk=0.2
Reasoning: "Night approaching, should preserve battery"
Intention: switch_to_grid (priority 8)
Plan:      [switch_power_source(GRID), set_soc_target(80)]
Action:    Switched to grid, SOC target 80%
Reflect:   "Battery preserved for night use"

Example 2: Storm Predicted
──────────────────────────
Telemetry: SOC 45%, Panel 15V, Grid OK
Beliefs:  weather=thunderstorm, predicted_risk=0.7, backup_time=2.1h
Reasoning: "High outage risk with insufficient backup"
Intention: switch_to_grid (priority 9)
Plan:      [switch_power_source(GRID), set_soc_target(85)]
Action:    Switched to grid, SOC target 85%
Reflect:   "Conserved battery for predicted outage"
Learn:     "Storms at 6PM → conserve battery"

Example 3: Morning Charge
─────────────────────────
Telemetry: SOC 35%, 9:00 AM, Panel 19V, Grid OK
Beliefs:  hours_to_sunset=10, can_charge=True, predicted_risk=0.1
Reasoning: "Good sun available, can fast charge"
Intention: switch_to_solar (priority 5)
Plan:      [switch_power_source(SOLAR), set_soc_target(90)]
Action:    Switched to solar, SOC target 90%
Reflect:   "Using solar to charge battery"

Example 4: Critical Battery (Emergency)
───────────────────────────────────────
Telemetry: SOC 18%, 11.0V, Grid OK
Beliefs:  battery_critical=True
Reasoning: "EMERGENCY: Critical battery level"
Intention: emergency_switch_to_grid (priority 10)
Plan:      [switch_power_source(GRID), send_alert(CRITICAL)]
Action:    Emergency switch to grid, alert sent
Reflect:   "Prevented battery damage"

Example 5: Panel Degradation Detected
─────────────────────────────────────
Telemetry: Panel peak today 112W, All-time peak 165W
Beliefs:  efficiency_drop=32%
Reasoning: "Panel efficiency has dropped over 30% from peak. Likely needs cleaning."
Intention: notify_maintenance (priority 4)
Plan:      [send_alert("warning", "Panel efficiency dropped 32%. Please clean panels.")]
Action:    Alert sent to user dashboard
Reflect:   "Notified user of required maintenance"
```
