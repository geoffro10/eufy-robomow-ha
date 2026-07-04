# Eufy Robomow — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration for the **Eufy E15** and **E18** robotic lawn mowers.

Control and monitor your Eufy mower directly from Home Assistant over your local network, with cloud-synced settings pulled straight from your Eufy account — no extra tools or manual key extraction required.

> Looking for protocol-level detail (DP mappings, notification/error codes, zone table, schedule format)? See **[FINDINGS.md](FINDINGS.md)** for the full reverse-engineering writeup.

---

## Features

| Entity | Type | Description |
|--------|------|-------------|
| Mower | `lawn_mower` | Start, pause, dock — with activity state (mowing / returning / docked / paused) |
| Battery | `sensor` | Battery level (%) |
| Mowed Area | `sensor` | Area covered in the current or last session |
| Mowing Progress | `sensor` | Real-time session completion % (from DP113 telemetry blob) |
| Return Progress | `sensor` | Return-to-base progress (%) |
| Session Distance | `sensor` | Distance traveled in the current session (m) |
| Network | `sensor` | WiFi / Cellular connection type |
| Signal Strength | `sensor` | WiFi signal strength (dBm) |
| Last Notification | `sensor` | Most recent mower notification code (see FINDINGS.md for the code table) |
| Error Code | `sensor` | Most recent error/fault code (e.g. robot lifted, robot trapped) |
| Cut Height | `number` | Blade height 25–75 mm, step 5 mm (local, instant) |
| Volume | `number` | Speaker volume 0–100 % (local) |
| Edge Distance | `number` | −15 to +15 cm — how far inside/outside the border wire the mower cuts. Displayable in inches via HA's per-entity unit setting. |
| Pad Direction | `number` | Mowing path angle 0–359° |
| Travel Speed | `select` | Mower driving speed: slow / normal / fast |
| Blade Speed | `select` | Blade motor speed: slow / normal / fast |
| Path Distance | `select` | Lane spacing: 8 cm / 10 cm / 12 cm (shown with inch equivalents) |
| Stop on Rain | `switch` | Pause mowing when rain is detected |
| Child Protection | `switch` | Enable child/pet protection mode |
| Smart No-Go Suggestions | `switch` | AI-assisted no-go zone suggestions |
| Mow Yellow Grass | `switch` | Allow mowing on dry/yellow grass |

> **Cloud entities** (edge distance, pad direction, speeds, path distance) require your Eufy account credentials. They are polled every 5 minutes and written back via the Tuya mobile API using a byte-safe read-modify-write — only the field you change is touched, every other setting (including ones this integration doesn't model) is preserved untouched.
>
> Some entities (generic raw DP sensors, map coverage) are **disabled by default** — enable them in HA if you want to explore unconfirmed data points.

---

## Prerequisites

- **Local network access** — the mower and Home Assistant must be on the same LAN (or the mower reachable via IP).
- **Eufy account** — required for cloud-managed settings. The same email/password you use in the Eufy Home app.
- HA **2024.1** or newer.

---

## Installation

### Via HACS (recommended)

1. Open HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add URL: `https://github.com/jnicolaes/eufy-robomow-ha` — category: **Integration**
3. Search for **Eufy Robomow** and install
4. Restart Home Assistant

### Manual

1. Copy the `custom_components/eufy_robomow/` folder into your HA `config/custom_components/` directory
2. Restart Home Assistant

---

## Configuration

Go to **Settings → Devices & Services → Add Integration → Eufy Robomow**.

**Step 1 — Sign in:**
Enter your Eufy account email and password. The integration will automatically discover all your devices and fetch their local keys.

**Step 2 — Select mower:**
Pick your mower from the dropdown and enter its local IP address (find it in your router's DHCP table or the Eufy app's device info screen).

That's it — no external tools, no manual key extraction. The device name shown in HA is pulled live from your Eufy account rather than hardcoded, so it will correctly show your mower's actual model/name (E15, E18, or whatever you've named it).

---

## How it works

- **Local polling** (every 10 s) via the [Tuya local protocol](https://github.com/jasonacox/tinytuya) for real-time status (battery, activity state, mowed area, etc.).
- **Cloud polling** (every 5 min, skipped automatically while the sun is below the horizon since the mower can't be operating) via the Tuya mobile API for settings stored as protobuf blobs, and for any DP not exposed over the local protocol.
- **Writes**: mow/pause/dock commands go over the local protocol instantly. Setting changes (cut height defaults, edge distance, speeds, path distance) go through the cloud API and are immediately reflected in the Eufy app.
- **Activity state** (mowing / returning / docked / paused) is derived from a small state machine, not a single DP — see [FINDINGS.md](FINDINGS.md) for why a naive reading of the status DPs produces false state changes, and how this integration avoids them.

---

## Known limitations

- **Zone identity is fully mapped and readable** (which zone exists, its schedule, timing, and days), but **creating or editing a mowing schedule from Home Assistant is not yet supported.** The Eufy app writes schedules through an encrypted cloud channel this integration hasn't been able to decode (see FINDINGS.md §8 for exactly what was tried and ruled out). Schedules must currently be created/edited in the Eufy app; once created, this integration can read and display them fully. Manual start/pause/dock work from HA regardless of zone configuration.
- **Map display** — live GPS map is not yet supported.

---

## Troubleshooting

- **Entities unavailable** — check that the IP address is correct and the mower is on WiFi (not cellular only).
- **Cloud settings not updating** — cloud data refreshes every 5 minutes (and is skipped overnight); changes made in the Eufy app will appear after the next refresh cycle.
- Enable **debug logging** for detailed output:

```yaml
# configuration.yaml
logger:
  logs:
    custom_components.eufy_robomow: debug
```

---

## Credits

Authentication and local-key discovery based on [eufy-clean-local-key-grabber](https://github.com/albaintor/eufy-clean-local-key-grabber).
Local protocol via [tinytuya](https://github.com/jasonacox/tinytuya).
