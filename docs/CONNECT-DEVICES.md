# Connect the Devices — the only work left

The software side of Jarvis-Hub is **built**: every automation, script, voice
intent, dashboard, and scaffold in this repo is written, validated, and
deployed. What remains is physical: pairing real hardware so the placeholder
entities come alive. This doc is the complete checklist — work top to bottom,
each section says exactly what to do and which sleeping features wake up.

Legend: 🟢 = works the moment it's paired (automations already reference it) ·
🟡 = paired + one entity-ID swap in the config.

---

## 1. Phone — HA Companion app (free, 10 min) 🟢

The single highest-value connection: presence, notifications, GPS.

1. Install **Home Assistant Companion** (iOS/Android), point it at
   `http://100.127.246.7:8123` (Tailscale — works from anywhere).
2. Log in as giovanni; allow location + notifications.
3. Settings → People → link Giovanni to the new phone device tracker.
4. In the repo config, `notify.mobile_app_phone` is used everywhere — if the
   app registers under another name (Settings → Devices → your phone →
   notify service), tell Devin or rename it to match.

**Wakes up:** who's-home tile, welcome-home greeting, leaving/arrival
automations, every phone notification (budget, thermal, outage, disk, meds,
doorbell…), sleep intelligence (phone charging + alarm sensors), actionable
notification buttons.

## 2. USB microphone (~$10–15) 🟢

The X1's built-in mic array is dead at the hardware level (empty ACPI table —
no Linux driver can see it). Any USB mic/conference puck fixes it.

1. Plug it in. 2. `sudo systemctl restart wyoming-satellite` (or reboot).
3. Say "hey Jarvis". The satellite auto-detects USB mics first.

**Wakes up:** hands-free voice — all ~60 built-in intents, from across the room.

## 3. Monster Illuminessence lights (Tuya) 🟡

1. Remove from the Monster app → re-pair in the **Smart Life** app.
2. HA: Settings → Devices & Services → Add Integration → **Tuya** → scan QR
   in Smart Life.
3. Real entities appear (e.g. `light.smart_bulb_1`). Swap the placeholders
   `light.living_room` / `light.bedroom` in `ha-config/` to the real IDs
   (or rename the entities themselves to those IDs — zero config edits).

**Wakes up:** room sliders + pop-ups, movie mode dimming, goodnight/leaving
scenes, circadian color temperature, gradual wake ramp, night lights,
emergency all-red flash, party mode, occupancy theater, welcome-home lights.

## 4. iHome AutoVac Juno (iHRV9) 🟡

1. Try pairing in **Smart Life** (same as lights) → Tuya integration grabs it.
2. If not: HACS → **tuya-local** custom integration (community-confirmed for
   iHome AutoVacs).
3. Swap `vacuum.juno` to the real entity ID (or rename the entity).

**Wakes up:** Juno scene button, scheduled cleaning, patrol/away mode,
"vacuum stuck" alerts, voice start/stop/dock.

## 5. Fire TV (~15 min) 🟡

1. Fire TV: Settings → My Fire TV → Developer Options → **ADB debugging ON**
   (tap the device name 7× under About if hidden). Give it a DHCP reservation.
2. HA: Add Integration → **Android TV** (ADB) → Fire TV's IP → accept the
   pairing prompt on the TV.
3. Swap `media_player.fire_tv` to the real entity ID.

**Wakes up:** movie-mode lighting reactions, "put on Netflix/YouTube" voice
launches, media tab controls, sleep timer, pause-the-TV, playing-state
presence.

## 6. Matter mystery device 🟢

HA already sees a Matter device broadcasting on the network. Find its QR /
setup code (on the device or its box) → Settings → Devices & Services →
the discovered Matter card → Configure → scan/enter code.

## 7. Zigbee dongle + sensors (~$25 + $10–15/sensor) 🟡

SONOFF ZBDongle-E into the X1 → Add Integration → **ZHA**. Then cheap sensors:

| Sensor | Wakes up |
|---|---|
| Smoke/CO detector | fire & CO emergency (all lights red + announce + phone) — swap `binary_sensor.smoke_detector` / `binary_sensor.co_detector` |
| Water-leak puck | leak alert — `binary_sensor.water_leak` |
| Door/window | "back door open" announcements — `binary_sensor.back_door` |
| Motion/mmWave | room presence, night lights, activity sensing — `binary_sensor.hallway_motion` |
| Vibration | mailbox watch, laundry-done — `binary_sensor.mailbox` |
| Zigbee button | physical scene buttons |

## 8. Cameras (RTSP, ~$40–60 each) 🟡

1. Add camera RTSP URLs to `frigate/config.yml` (template inside).
2. `docker compose --profile cameras up -d`
3. Add Integration → **Frigate** (+ **MQTT** for events).

**Wakes up:** person-at-door announcements + snapshots, Cameras tab feeds,
face recognition & guest greeting scaffolds (add Double Take), security mode.
The X1's own webcam already streams via `bootstrap/setup-satellite.sh`.

## 9. Thermostat / HVAC (if the home gets one) 🟡

Any HA-supported thermostat → swap `climate.thermostat`.
**Wakes up:** arrival pre-heat, storm prep HVAC moves, HVAC filter tracking.

## 10. Doorbell 🟡

Any doorbell exposing a binary sensor (Zigbee button behind the chime works)
→ swap `binary_sensor.doorbell`, drop a chime mp3 at
`ha-config/media/jarvis_doorbell.mp3`, uncomment the block in
`ha-config/automations/location.yaml`.

## 11. Accounts (no hardware, 5 min each) — optional

| Account | How | Wakes up |
|---|---|---|
| Spotify | Add Integration → Spotify | mood music (R&B evenings / lo-fi focus), party-mode playlist |
| Google Calendar | Add Integration → Google | meeting prep from your real calendar (local Jarvis Calendar already live) |
| Waze/commute | Add Integration → Waze Travel Time + a Work zone | heavy-traffic heads-up |
| Plex or Jellyfin | `docker compose --profile media` (or `jellyfin`) `up -d` | movie library on the Media tab |
| NFC stickers (~$0.30) | Companion app → write tag | tap-nightstand = bedtime, tap-door = leaving |

---

## Already alive (nothing to connect)

Voice pipeline (Whisper/Piper/openWakeWord — speakers work, mic needs §2),
Ollama LLM brain, LifeOS (health/money/briefing at :8090), morning briefing +
weather (coordinates set), budget alert, all reminders/timers, shopping +
errands lists, Jarvis Calendar + meeting prep, kiosk dashboard (HUD theme,
avatar, splash, Home/Keyboard buttons), System Monitor vitals + thermal/disk
guards, battery-UPS outage alerts, nightly backups, Tailscale remote access,
fun pack + personality, context memory, sleep/guest/vacation modes.

Full architecture + phase detail: [PLAN.md](PLAN.md) · Day-one commands:
[README — Build status](../README.md#build-status--where-we-are)
