"""
pico_client.py — MicroPython client for the Pico W

Runs on the microcontroller. Connects to the server via WebSocket,
sends telemetry every INTERVAL seconds, and acts on received commands.

Usage:
  Copy this file to your Pico W as main.py (after editing CONFIG below).
  Requires: micropython-uasyncio, uwebsockets (install via mip or bundle manually)
"""

import json
import time
import network
import uasyncio as asyncio

# ── Import configuration from config.py ───────────────────────────
try:
    from config import WIFI_SSID, WIFI_PASSWORD, SERVER_URL, API_KEY
    # Convert HTTP to WebSocket URL
    SERVER_WS_URL = SERVER_URL.replace("http://", "ws://").replace("https://", "wss://")
    if not SERVER_WS_URL.endswith("/ws/pico"):
        SERVER_WS_URL = SERVER_WS_URL.rstrip("/") + "/ws/pico"
    CONFIG = {
        "ssid": WIFI_SSID,
        "password": WIFI_PASSWORD,
        "server_url": SERVER_WS_URL,
        "api_key": API_KEY,
        "interval_s": 30,
    }
except ImportError:
    # Fallback to hardcoded values if config.py import fails
    CONFIG = {
        "ssid": "YOUR_WIFI_SSID",
        "password": "YOUR_WIFI_PASSWORD",
        "server_url": "ws://YOUR_SERVER_IP:8000/ws/pico",
        "api_key": "YOUR_API_KEY",
        "interval_s": 30,
    }

# ── GPIO / hardware pins (adjust to your wiring) ──────────────────
import machine

# Relays
RELAY_GRID_N = machine.Pin(0, machine.Pin.OUT)  # Grid Neutral
RELAY_GRID_L = machine.Pin(1, machine.Pin.OUT)  # Grid Line
RELAY_INV_N = machine.Pin(2, machine.Pin.OUT)   # Inverter Neutral
RELAY_INV_L = machine.Pin(3, machine.Pin.OUT)   # Inverter Line

# Status LEDs
LED_PIN = machine.Pin("LED", machine.Pin.OUT)   # Heartbeat
LED_WIFI = machine.Pin(6, machine.Pin.OUT)      # WiFi Status
LED_SOLAR = machine.Pin(7, machine.Pin.OUT)     # Solar Mode
LED_GRID = machine.Pin(8, machine.Pin.OUT)      # Grid Mode
LED_FAULT = machine.Pin(9, machine.Pin.OUT)     # Fault Status

# ── ADC channels (adjust to your voltage dividers / shunts) ──────
BAT_V_ADC   = machine.ADC(26)
PANEL_V_ADC = machine.ADC(27)
GRID_OK_PIN = machine.Pin(5, machine.Pin.IN, machine.Pin.PULL_UP)

# ADC scale factors — calibrate to your hardware
BAT_V_SCALE   = 15.0 / 65535   # e.g. 1:5 divider on 3V3 ref
PANEL_V_SCALE = 24.0 / 65535
BAT_SHUNT_SCALE = 0.05          # amps per ADC unit — needs INA219 in real life


def read_telemetry(switches: int, source: str) -> dict:
    bat_v   = BAT_V_ADC.read_u16()   * BAT_V_SCALE
    panel_v = PANEL_V_ADC.read_u16() * PANEL_V_SCALE
    grid_ok = bool(GRID_OK_PIN.value())

    # Crude SOC estimate from voltage (12V lead-acid)
    soc = max(0, min(100, (bat_v - 10.5) / (12.8 - 10.5) * 100))

    return {
        "type":     "telemetry",
        "bat_v":    round(bat_v, 3),
        "bat_i":    0.0,                 # Replace with INA219 reading
        "bat_soc":  round(soc, 1),
        "panel_v":  round(panel_v, 3),
        "panel_i":  0.0,                 # Replace with INA219 reading
        "grid_ok":  grid_ok,
        "source":   source,
        "switches": switches,
    }


async def switch_source(target: str):
    """Safe break-before-make source switching logic."""
    print(f"[HW] Initiating switch to {target}")
    
    # Break phase: Turn off all relays first
    RELAY_INV_L.value(1) # Assuming active low relays
    RELAY_INV_N.value(1)
    RELAY_GRID_L.value(1)
    RELAY_GRID_N.value(1)
    
    # 500ms safety delay (break-before-make)
    await asyncio.sleep_ms(500)
    
    # Make phase: Turn on the target relays
    if target == "GRID":
        RELAY_GRID_N.value(0)
        RELAY_GRID_L.value(0)
        LED_GRID.value(1)
        LED_SOLAR.value(0)
    elif target == "SOLAR":
        RELAY_INV_N.value(0)
        RELAY_INV_L.value(0)
        LED_SOLAR.value(1)
        LED_GRID.value(0)
        
    print(f"[HW] Switched to {target} completed")


async def apply_command(cmd: dict, current_source: str, switches: int):
    cmd_type = cmd.get("type")

    if cmd_type == "SWITCH_SOURCE":
        target = cmd.get("source", current_source)
        if target != current_source:
            await switch_source(target)
            current_source = target
            switches += 1
            print(f"[CMD] Switched to {target}: {cmd.get('reason','')}")

    elif cmd_type == "SET_SOC_TARGET":
        print(f"[CMD] SOC target updated to {cmd.get('target')}%")

    elif cmd_type == "auth_ok":
        print("[AUTH] Server confirmed authentication")

    return current_source, switches


async def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(CONFIG["ssid"], CONFIG["password"])
    
    LED_WIFI.value(0) # Blinking while connecting
    for _ in range(30):
        if wlan.isconnected():
            print(f"[WIFI] Connected — IP: {wlan.ifconfig()[0]}")
            LED_WIFI.value(1) # Solid when connected
            return True
        LED_WIFI.toggle()
        await asyncio.sleep(1)
    print("[WIFI] Failed to connect")
    LED_WIFI.value(0)
    return False


async def run():
    if not await connect_wifi():
        machine.reset()

    # Import here so MicroPython doesn't fail on import for missing lib
    import uwebsockets.client as websockets  # type: ignore

    source   = "SOLAR"
    switches = 0

    while True:
        try:
            print(f"[WS] Connecting to {CONFIG['server_url']} …")
            async with websockets.connect(CONFIG["server_url"]) as ws:
                # ── Handshake ──────────────────────────────────
                await ws.send(json.dumps({"type": "auth", "key": CONFIG["api_key"]}))

                last_send = 0
                while True:
                    LED_PIN.toggle()

                    now = time.time()
                    if now - last_send >= CONFIG["interval_s"]:
                        telemetry = read_telemetry(switches, source)
                        await ws.send(json.dumps(telemetry))
                        last_send = now
                        print(f"[TX] SOC={telemetry['bat_soc']}% panel={telemetry['panel_v']}V")

                    # Non-blocking recv
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        if msg:
                            cmd = json.loads(msg)
                            source, switches = await apply_command(cmd, source, switches)
                    except asyncio.TimeoutError:
                        pass

                    await asyncio.sleep(1)

        except Exception as exc:
            print(f"[WS] Error: {exc} — reconnecting in 10 s…")
            await asyncio.sleep(10)


asyncio.run(run())
