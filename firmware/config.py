""""
Smart Solar PCU - Configuration File
Edit these values for your specific setup
"""

# ============ NETWORK CONFIGURATION ============
WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"
SERVER_URL = "http://your-server-ip:8000"
API_KEY = "your-api-key"

# ============ BATTERY CONFIGURATION ============
# Lead-Acid Battery Specs
BATTERY_VOLTAGE = 12          # Nominal voltage (12V or 24V)
BATTERY_CAPACITY_AH = 40      # Capacity in Ah
BATTERY_C_RATING = 10         # C10, C20, etc.

# Voltage Thresholds (for 12V system, multiply by 2 for 24V)
V_FLOAT = 13.8                # Float charge voltage
V_BULK = 14.4                 # Bulk charge voltage  
V_LOW = 11.5                  # Low battery warning
V_CRITICAL = 11.0             # Critical cutoff

# SOC Thresholds
SOC_MIN_OPERATIONAL = 30      # Minimum SOC for normal operation
SOC_CRITICAL = 20             # Critical SOC level
SOC_OPTIMAL = 80              # Target SOC for night reserve

# ============ PANEL CONFIGURATION ============
PANEL_WATTS = 330             # Total panel wattage (2x165W)
PANEL_VOLTAGE_NOMINAL = 12    # Nominal panel voltage
V_PANEL_MIN = 15.0            # Minimum voltage to consider active
V_PANEL_ACTIVE = 18.0         # Good charging voltage

# ============ CURRENT SENSOR ============
# ACS712-05 Configuration
CURRENT_SENSOR_ZERO_VOLT = 1.65   # 0A output (VCC/2)
CURRENT_SENSITIVITY = 0.185       # V/A (185mV/A for ACS712-05)
MAX_CHARGE_CURRENT = 4.0          # Max charge (C10 for 40Ah = 4A)
MAX_DISCHARGE_CURRENT = 8.0       # Max discharge current

# ============ CALIBRATION ============
# Voltage Divider Ratios (R1+R2)/R2
V_BAT_DIVIDER_RATIO = 11.0        # Battery voltage divider
V_PANEL_DIVIDER_RATIO = 21.0      # Panel voltage divider

# Fine-tuning offsets (measured with multimeter)
V_BAT_OFFSET = 0.0
V_PANEL_OFFSET = 0.0

# ============ SWITCHING PARAMETERS ============
SWITCH_DELAY_MS = 500         # Delay between relay operations
COOLDOWN_MS = 5000            # Minimum time between switches
MAX_SWITCHES_PER_HOUR = 10    # Rate limiting

# ============ PIN CONFIGURATION ============
# Relays (Active LOW typical for relay modules)
RELAY_GRID_N = 0              # GP0 - Grid Neutral
RELAY_GRID_L = 1              # GP1 - Grid Line  
RELAY_INV_N = 2               # GP2 - Inverter Neutral
RELAY_INV_L = 3               # GP3 - Inverter Line

# Sensors
ADC_BATTERY_VOLT = 26         # ADC0 - Battery voltage
ADC_PANEL_VOLT = 27           # ADC1 - Panel voltage
ADC_BATTERY_CURRENT = 28      # ADC2 - Battery current

# Digital I/O
GRID_SENSE_PIN = 5            # GP5 - Grid presence detection
LED_WIFI_PIN = 6              # GP6 - WiFi status
LED_SOLAR_PIN = 7             # GP7 - Solar active
LED_GRID_PIN = 8              # GP8 - Grid active
LED_FAULT_PIN = 9             # GP9 - Fault indicator

# ============ OPERATING MODES ============
MODE_AUTO = "AUTO"
MODE_SOLAR_PRIORITY = "SOLAR_PRIORITY"
MODE_GRID_PRIORITY = "GRID_PRIORITY"
MODE_SOLAR_ONLY = "SOLAR_ONLY"

DEFAULT_MODE = MODE_AUTO

# ============ TIMING ============
UPDATE_INTERVAL_MS = 1000     # Main loop interval
TELEMETRY_INTERVAL_MS = 10000 # Send data to server
AI_FETCH_INTERVAL_MS = 300000 # Fetch predictions (5 min)
SENSOR_SAMPLES = 10           # ADC samples for averaging
"
