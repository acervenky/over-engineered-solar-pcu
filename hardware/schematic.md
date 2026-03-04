# Smart Solar PCU - Hardware Schematic & Wiring Guide

## Overview
Transfer switch design with isolated Grid and Inverter N/L lines using 4 relays.

## Pinout (Raspberry Pi Pico W)

| Pin | GPIO | Function | Description |
|-----|------|----------|-------------|
| 1   | GP0  | RELAY_GRID_N | Grid Neutral relay |
| 2   | GP1  | RELAY_GRID_L | Grid Line relay |
| 4   | GP2  | RELAY_INV_N  | Inverter Neutral relay |
| 5   | GP3  | RELAY_INV_L  | Inverter Line relay |
| 7   | GP5  | GRID_SENSE   | Grid presence detection |
| 9   | GP6  | LED_WIFI     | WiFi status LED |
| 10  | GP7  | LED_SOLAR    | Solar active LED |
| 11  | GP8  | LED_GRID     | Grid active LED |
| 12  | GP9  | LED_FAULT    | Fault indicator LED |
| 31  | ADC0 | BAT_VOLTAGE  | Battery voltage (GPIO26) |
| 32  | ADC1 | PANEL_VOLTAGE| Panel voltage (GPIO27) |
| 34  | ADC2 | BAT_CURRENT  | Battery current (GPIO28) |
| 36  | 3V3  | ADC_REF      | 3.3V reference |
| 38  | GND  | GND          | Ground |
| 40  | VBUS | VBUS/5V      | USB power input |

## Voltage Sensor Circuits

### Battery Voltage (0-20V range, scaled to 0-3.3V)
```
Battery (+) ---[10k]---+---[1k]--- GND
                       |
                      ADC0 (GPIO26)
                       
Divider ratio: 11:1
V_adc = V_bat / 11
```

### Panel Voltage (0-40V range, scaled to 0-3.3V)
```
Panel (+) ---[20k]----+---[1k]--- GND
                      |
                     ADC1 (GPIO27)
                      
Divider ratio: 21:1
V_adc = V_panel / 21
```

## Current Sensor Circuit

### ACS712-05 (+-5A range) for Battery Current
```
Battery (-) ---[ACS712]--- Load (-)
                |
               Vout --- ADC2 (GPIO28)
                |
               2.5V = 0A
               1.85V = -5A
               3.15V = +5A
               Sensitivity: 185mV/A
```

## Relay Circuit

### 5V Relay Module (4-channel, active LOW)
```
Pico GPIO0 ----> Relay1 IN (Grid N)
Pico GPIO1 ----> Relay2 IN (Grid L)
Pico GPIO2 ----> Relay3 IN (Inv N)
Pico GPIO3 ----> Relay4 IN (Inv L)

Relay VCC  ----> 5V supply
Relay GND  ----> Common GND

**IMPORTANT: Use external 5V supply for relays, not Pico VBUS**
```

## Transfer Switch Wiring

### Grid Input (from Mains)
```
Mains N -----> Relay1 COM
              Relay1 NC ----> LOAD_N (to house)
              Relay1 NO ----> (not connected)

Mains L -----> Relay2 COM
              Relay2 NC ----> LOAD_L (to house)
              Relay2 NO ----> (not connected)

Default: Grid connected (NC contacts)
```

### Inverter Output
```
Inverter N -----> Relay3 COM
                 Relay3 NO ----> LOAD_N (to house)
                 Relay3 NC ----> (not connected)

Inverter L -----> Relay4 COM
                 Relay4 NO ----> LOAD_L (to house)
                 Relay4 NC ----> (not connected)

Default: Inverter disconnected (NO contacts open)
```

### Load Output
```
LOAD_N: Connected to Relay1 NC and Relay3 NO
LOAD_L: Connected to Relay2 NC and Relay4 NO

**NEVER connect both sources simultaneously!**
Software enforces break-before-make switching.
```

## Grid Presence Detection

### Optocoupler Method (Recommended for safety)
```
Mains L ---[100k]---+---[Opto LED-]---[Diode]--- Mains N
                    |
             [Opto LED+]
                    |
             [Opto Transistor]
                    |
           GPIO5 (with pull-up)
                    |
                  GND

When mains present: Transistor ON, GPIO5 = LOW
When mains absent:  Transistor OFF, GPIO5 = HIGH (pull-up)
```

### Alternative: 12V Adapter Method
```
12V AC-DC Adapter output ---[Voltage divider]--- GPIO5

Adapter ON when grid present -> GPIO5 HIGH
Adapter OFF when grid out -> GPIO5 LOW
```

## Power Supply

### Recommended: Dual Supply
```
1. USB Power Bank (5V, 2A) -> Pico VBUS
   - Powers Pico W
   - Powers sensors
   
2. External 5V/2A Adapter -> Relay Module VCC
   - Powers 4 relays
   - Common GND with Pico
```

### Single Supply Option
```
12V Battery -> [Buck Converter 5V/3A] -> Power everything
              -> Pico VBUS (5V)
              -> Relay Module VCC
              -> ACS712 VCC
```

## Complete Wiring Diagram

```
                                    GRID
                                     |
            +------------------------+------------------------+
            |                        |                        |
           [R1]                   [R2]                       |
            |                        |                        |
     Relay1 COM               Relay2 COM                     |
      |      |                 |      |                      |
     NC     NO                NC     NO                     |
      |                       |                              |
      +------LOAD_N-----------+                              |
      |                       |                              |
      +------LOAD_L-----------+                              |
                                                             |
                                    INVERTER                 |
                                     |                       |
            +------------------------+----------------+      |
            |                        |                |      |
           [R3]                   [R4]               |      |
            |                        |                |      |
     Relay3 COM               Relay4 COM             |      |
      |      |                 |      |              |      |
     NC     NO                NC     NO              |      |
      |                       |                      |      |
      +-----------------------+                      |      |
                                                     |      |
                                              +------+------+
                                              |
                                         [Load/Fans/LEDs]

R1=Grid N, R2=Grid L, R3=Inv N, R4=Inv L
NC=Normally Closed, NO=Normally Open
```

## Safety Considerations

1. **Double-pole switching**: Both N and L switched simultaneously
2. **Break-before-make**: 500ms delay between switching to prevent shorts
3. **Relay rating**: Use 10A/250V AC relays minimum
4. **Fusing**: Add 5A fuses on both Grid and Inverter inputs
5. **Isolation**: Keep high voltage (AC) separate from low voltage (DC) wiring
6. **Enclosure**: Use IP65 rated box for outdoor/indoor safety

## Calibration Notes

### Voltage Calibration
Measure actual battery voltage with multimeter, adjust V_BAT_DIV:
```python
actual_voltage = 12.6
measured_adc = read_adc(ADC_BAT_V)
measured_voltage = measured_adc * 3.3 * V_BAT_DIV / 65535
# Adjust V_BAT_DIV = actual_voltage / (measured_adc * 3.3 / 65535)
```

### Current Calibration (Zero Offset)
With no current flowing:
```python
raw_zero = read_adc(ADC_BAT_I)
I_ZERO = (raw_zero / 65535) * 3.3  # Should be ~1.65V
```

