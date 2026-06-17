# EOS Sauna Home Assistant Integration

A custom Home Assistant integration for EOS Sauna devices (U-Command control panels) via the EOS cloud API.

---

## Supported devices

- **EOS U-Command** — full control (climate, light, sessions)
- **EOS EmoTec Cloud** — full control (climate, light, sessions)
- **EOS Compact Cloud** — full control (climate, light, sessions)

## Features

- Climate control - Set target temperature/humidity, switch profiles and turn sauna on/off 
- Accessory control - Control light states 
- Session Tracking - Last session duration, weekly session count 
- Environmental measurements (temperature, humidity)

## Entities

### Climate 
| Entity | Description |
|--------|-------------|
| **Sauna** | Set target temp/hum, switch presets, turn on/off |


### Light 
Turn the sauna light on/off


### Sensors

| Sensor | Description |
|--------|-------------|
| **Temperature** | Current measured sauna temperature (°C) |
| **Humidity** | Current measured humidity (%)|
| **Heater On For** | How long the heater will be on (minutes) |
| **Ready In** | Estimated time until target temperature is reached (minutes) |
| **Last Session Duration** | Length of the most recently completed sauna session (minutes) |
| **Sessions This Week** | Number of distinct sauna sessions since Monday 00:00 local time |
| **Total Hours** | Total hours of how long sauna has been on |
|

### Binary sensors

| Sensor | Device | Description |
|--------|--------|-------------|
| **Steamer** | Steamer on/off |


## Requirements

- A EOS Cloud ready control panel 
- An active **EOS Cloud** account with at least one paired device
- Internet access 

---

## Installation

### HACS (Recommended)

1. Open HACS → **Integrations → Top right ⋮ → Custom repositories**
2. Add `https://github.com/Marcel-Klug/eos-home-assistant-plugin` to the repository and select type as Integration
3. Search for "EOS Sauna" and Download
4. Restart Home assistant
5. Continue with [Configuration](#configuration) below.

### Manual

1. Clone this repository.
2. Copy the `custom_components/eos/` folder into your Home Assistant config directory so the path reads:
   ```
   <config>/custom_components/eos/
   ```
3. Restart Home Assistant.
4. Continue with [Configuration](#configuration) below.

---

## Configuration

1. In Home Assistant, go to **Settings → Devices & Services → Add Integration**.
2. Search for **EOS Sauna** and select it.
3. Enter your **EOS Account email address** and **password**.
4. Click **Submit**. The integration will authenticate and discover your sauna(s) automatically.


### Options (Optional)

After setup, click **Configure** on the integration card to adjust:

| Option | Default | Description |
|--------|---------|-------------|
| Polling interval (seconds) | 60 | How often Home Assistant fetches fresh data from the EOS Cloud API |

---

## Usage Notes

- **Session counting** — The *Sessions This Week* sensor queries the EOS telemetry history API each poll cycle and counts distinct session IDs since Monday 00:00 local time.
- **Last Session Duration** — This is tracked locally by the integration from sauna on/off edges. It persists only for the current Home Assistant run; restarting HA resets it.

---

## Tools

Setup tools with commands:
   ```
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

### ruff
   ```
   ruff check .
   ```
   
### mypy
   ```
   mypy custom_components/eos
   ```

---

## Troubleshooting

**Invalid email address or password.**
Verify that your credentials are right and that you have an active EOS account

**Cannot connect** 
Verify EOS account credentials and check internet

**This EOS account is already configured.** 
You have already added your EOS account

---

## Contributing

Pull requests and issues are welcome. Please open an issue before starting large changes so we can discuss the approach first.
