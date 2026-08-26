# Jarvis Hub Build Plan — ThinkPad X1 Tablet Gen 3

Goal: replace Alexa/Google Home with a private, local-first hub running Home Assistant, controlling your **Monster Illuminessence lights** and **iHome AutoVac Juno (iHRV9)**, with an offline voice assistant.

> **BUILD STATUS: software foundation implemented.** The repository contains HA,
> LifeOS, local voice, optional LLM, kiosk UI, context engine, safety policies,
> simulations, backups, and remote-access setup. Deployment to the physical X1,
> Linux hardware verification, credential setup, entity mapping, and device pairing
> remain commissioning work. Follow
> **[CONNECT-DEVICES.md](CONNECT-DEVICES.md)** — it maps every device to the
> exact steps and the dormant features that wake up when it's connected.

---

## Phase 0 — Prep the laptop (~1 hour)

1. Install **Ubuntu Server 24.04 LTS** (or Debian 12) on the X1. Wipe Windows unless you need it.
2. During install: enable OpenSSH so you can manage it from another computer.
3. Keep the lid-close behavior from suspending the machine:
   - Edit `/etc/systemd/logind.conf` → set `HandleLidSwitch=ignore`, then `sudo systemctl restart systemd-logind`.
4. Give it a static IP on your router (DHCP reservation) so its address never changes.
5. Install Docker:
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   ```

## Phase 1 — Install Home Assistant (~30 min)

Run Home Assistant Container:

```bash
docker run -d \
  --name homeassistant \
  --restart=unless-stopped \
  --privileged \
  --network=host \
  -e TZ=America/New_York \
  -v /opt/homeassistant:/config \
  ghcr.io/home-assistant/home-assistant:stable
```

Then open `http://<laptop-ip>:8123` from any device on your WiFi, create your account, and finish onboarding.

> We use **Ubuntu + Docker** (not Home Assistant OS) on purpose: the X1 runs more than
> HA — LifeOS, Grocy, and Ollama live beside it in the same stack, and the laptop's own
> screen doubles as a wall-hub display (`bootstrap/setup-kiosk.sh`). HAOS would lock the
> machine down to HA only.

## Phase 2 — Connect your devices

### Monster Illuminessence lights (Tuya-based)

1. Delete the lights from the Monster app, then re-pair them in the **Smart Life** app (they're Tuya devices under the hood — confirmed working by the HA community).
   - Caveat: a few of the *newest* Monster models only pair with the Monster app. If Smart Life won't find yours, tell me the exact model and we'll use a workaround.
2. In Home Assistant: **Settings → Devices & Services → Add Integration → Tuya**, scan the QR code with Smart Life. Your lights appear as entities (on/off, brightness, color).
3. Optional upgrade later: **LocalTuya** (via HACS) for fully local control with no cloud dependency.

### iHome AutoVac Juno iHRV9 (iHome Clean app, Tuya platform)

1. Try re-pairing the vacuum in the **Smart Life** app first (same as the lights). If it pairs, the Tuya integration picks it up automatically.
2. If Smart Life won't pair it, use **tuya-local** (HACS custom integration) — the community has gotten iHome AutoVac models working this way. Basic controls (start/stop/dock/locate) work; fancy stats like battery % can be spotty on iHome models.
3. Worst case you keep the iHome Clean app for maps and use HA for start/stop/schedules.

## Phase 3 — Local voice assistant (beats Alexa on privacy)

1. In HA: set up the **Assist** pipeline (Settings → Voice assistants).
2. Run Whisper (speech-to-text) + Piper (text-to-speech) as Docker containers on the X1:
   ```bash
   docker run -d --restart=unless-stopped -p 10300:10300 \
     rhasspy/wyoming-whisper --model small-int8 --language en
   docker run -d --restart=unless-stopped -p 10200:10200 \
     rhasspy/wyoming-piper --voice en_GB-alan-medium --length-scale 1.03 \
       --noise-scale 0.45 --noise-w-scale 0.70 --sentence-silence 0.18
   ```
3. Add both in HA via the **Wyoming** integration (point at the laptop's IP, ports 10300/10200).
4. Now: "turn on the living room lights", "start the vacuum" — all processed locally on the X1, nothing sent to Amazon/Google.

Microphones around the house (pick one):
- **Your phone**: the HA Companion app has Assist built in — free, start here.
- **ESP32-S3-BOX-3** (~$50) or **HA Voice Preview Edition** (~$59): dedicated wake-word satellite ("Hey Jarvis") per room.
- An old Android phone on a stand running the HA app in always-on mode.

## Phase 4 — Automations (where it beats Alexa)

Examples you can build in the HA UI, no code:
- Vacuum runs weekdays at 10am, but only if nobody's home (phone presence detection).
- Lights fade to warm at sunset; everything off at midnight.
- "Movie mode": one voice command dims lights.
- Lights flash when the vacuum finishes or gets stuck.

## Phase 5 — Presence detection (know who's home)

The foundation for "smarter than Alexa" automations.

1. Install the **Home Assistant Companion app** on every household phone — it reports location, WiFi connection, battery, and more as sensors.
2. In HA: **Settings → People**, link each person to their phone's device tracker.
3. Add a backup layer: the **UniFi/router integration** or **ping** integration tracks phones by whether they're on your WiFi (works even with location off).
4. Create zone-based automations:
   - Everyone leaves → all lights off, vacuum starts, "away mode" on.
   - First person arrives home after sunset → hallway lights on.
   - Optional: a $20 Zigbee motion sensor per room (see Phase 11) for room-level presence.

## Phase 6 — Advanced automations (conditions, timers, chained logic)

Build these in the visual editor (Settings → Automations) — no code needed, but unlimited depth:

- **Conditions**: "IF after sunset AND someone is home AND the TV is playing → dim living room lights to 20%."
- **Wait/timer steps**: "When the last person leaves, wait 10 minutes (in case they come back), then run the vacuum."
- **Chained triggers**: vacuum finishes → lights flash green → phone notification with a photo-worthy summary.
- **Modes/scenes**: define "Movie", "Bedtime", "Party" scenes; trigger by voice, button, or schedule.
- **Templating (later)**: Jinja templates let you compute anything — "turn on lights at 30% × (how dark it is outside)".

## Phase 7 — Dashboards (a real control center)

1. HA's built-in **Lovelace dashboards** are fully customizable: per-room cards for lights, a vacuum card with start/dock buttons, presence, history graphs.
2. Install **HACS** (Home Assistant Community Store) to get community cards — e.g. a vacuum map card, button cards, themes:
   ```bash
   docker exec -it homeassistant bash -c "wget -O - https://get.hacs.xyz | bash -"
   ```
   Then restart HA and add HACS via Settings → Devices & Services.
3. Wall-mount option: any old tablet running the HA app in kiosk mode (or a cheap used Fire tablet with Fully Kiosk Browser) becomes a wall control panel.

## Phase 8 — LLM voice assistant (the fun one)

Make your assistant actually converse and reason — not canned responses.

**Option A — Fully local (private, slower):**
1. Install Ollama on the X1:
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ollama pull llama3.2:3b
   ```
2. In HA: add the **Ollama integration** (Settings → Devices & Services → Add → Ollama, URL `http://<laptop-ip>:11434`).
3. In your Assist pipeline, set the conversation agent to Ollama and enable **"Control Home Assistant"** so it can act on your devices.
4. Reality check: the X1 Gen 3's CPU runs a 3B model at a few words/second — fine for commands and short answers, not for essays.

**Option B — Cloud LLM (smarter, ~pennies/day):** add the **Anthropic (Claude)** or **OpenAI** integration instead and use it as the conversation agent. Voice audio is still processed locally by Whisper; only the text goes to the API.

Now you can say things like "make it cozy in here" or "is the house ready for the night?" and it figures out what to do.

**Making it a real conversation (not one-shot commands):**
- Assist supports **multi-turn conversation**: it keeps context within a session, so you can say "turn on the lights" → "actually make them warmer" → "perfect, now the vacuum" and it follows along.
- Enable **follow-up mode** on voice satellites ("continue conversation") so it keeps listening after answering — no wake word needed for the follow-up.
- The LLM agent can **ask you clarifying questions back** ("Which room's lights?") and handles chit-chat, questions, opinions — a genuine conversation partner, not Alexa's canned one-liners.
- Give it a personality + standing instructions in the system prompt (see Phase 13 Tier 5), and with a cloud LLM (Option B) the conversation quality is honestly beyond Alexa/Google by a wide margin.
- For long-term memory ("remember that I park in spot 22B"), add memory via the LLM integration's prompt template or a HACS memory add-on — it can recall facts across conversations.

## Phase 9 — Custom wake word

1. HA's wake-word engine is **openWakeWord** (or **microWakeWord** on ESP32 satellites). Built-in options: "Hey Jarvis", "Alexa", "Hey Mycroft", "Okay Nabu".
2. Run it on the X1:
   ```bash
   docker run -d --restart=unless-stopped -p 10400:10400 \
     rhasspy/wyoming-openwakeword --preload-model 'hey_jarvis'
   ```
   Add via the Wyoming integration, then select the wake word in your Assist pipeline.
3. Train your own custom phrase with the openWakeWord community Colab notebook (free, ~1 hour) — any phrase you want.

## Phase 10 — Actionable notifications & history

- **Actionable notifications**: automations can send phone notifications with buttons — "Vacuum stuck under the couch" → [Send home] [Ignore]. Configured in the automation's notify action with `actions:` — the button press triggers a follow-up automation.
- **History & analytics**: HA records everything automatically — check the History and Logbook tabs for when lights were on, vacuum runs, presence timeline. Add the **Energy dashboard** if you later get smart plugs with power monitoring.
- **Long-term stats**: data is kept in a local database on the X1 (nobody else sees it). For years of history, add InfluxDB + Grafana later.

## Phase 11 — Break brand lock-in (grow in any direction)

- **Zigbee USB dongle** (~$20–35, e.g. SONOFF ZBDongle-E) + the ZHA integration = cheap sensors, buttons, and bulbs (IKEA, Aqara, Sonoff, ThirdReality) with no WiFi, no cloud, no accounts.
- **3000+ integrations**: your router, Google Calendar, weather, car (many brands), TV, game consoles, gas prices, even the ISS position — browse Settings → Add Integration.
- Mix anything: Tuya + Hue + Zigbee + Matter devices all appear side-by-side and work in the same automations.

## Phase 12 — Remote access & resilience

- **Tailscale** (free VPN): control everything from anywhere without exposing your network. Install on the X1 and your phone, done.
  - Or **Nabu Casa** ($6.50/mo): easiest remote access + funds HA development.
- **Offline by design**: with LocalTuya (Phase 2 upgrade) + local voice (Phase 3) + local LLM (Phase 8A), the entire system works with the internet down.
- **Backups**: enable HA's automatic backups (Settings → System → Backups) and copy them off the laptop occasionally.
- **No enshittification**: no ads, no data selling, no features removed behind paywalls — open source only gets better.

---

## Phase 13 — The Jarvis Roadmap (Ironman/Batman mode)

Everything below builds on Phases 0–12. Add in this order — each tier makes the next one better.

### Tier 1 — Give Jarvis a voice and ears everywhere (~$60–120)

| Item | Cost | What it unlocks |
|---|---|---|
| **HA Voice Preview Edition** (1–2 units) | $59 ea | Wake-word satellite per room — say "Jarvis..." from the couch, kitchen, bedroom |
| *(budget alt)* ESP32-S3-BOX-3 | ~$50 | Same job, slightly more DIY |
| Custom wake word "Jarvis" | Free | Phase 9 — train with openWakeWord |
| TTS voice upgrade | Free | Pick a Piper voice you like, or use a cloud TTS for a truly cinematic voice |

Result: talk to the house naturally from anywhere, it answers back and acts (LLM from Phase 8 does the reasoning).

### Tier 2 — The house senses everything (~$70–150)

| Item | Cost | What it unlocks |
|---|---|---|
| **SONOFF ZBDongle-E** (Zigbee) | ~$25 | Required hub for all the cheap sensors below |
| **mmWave presence sensor** (Aqara FP2, or FP300) | ~$25–80 | Detects you even sitting perfectly still — lights follow you room to room, no switches ever |
| Zigbee **door/window sensors** (3-pack) | ~$25 | Knows every door state; "you left the back door open" announcements |
| Zigbee **motion + temp sensors** | ~$15 ea | Per-room presence, temperature-driven automations |
| Zigbee **buttons** | ~$10 ea | Physical "scene" buttons — one tap = Movie mode |

Result: the house *knows* where everyone is and what state everything's in — the foundation of all Batman-cave behavior.

### Tier 3 — Eyes: cameras with local AI (~$80–150)

1. Any cheap RTSP-capable camera (Amcrest/Reolink, ~$40–60 each — avoid cloud-only brands like Ring/Nest).
2. Run **Frigate** in Docker on the X1 — local object detection (person/car/dog/package), zero cloud.
3. Add a **Google Coral USB accelerator** (~$40 used) — offloads AI detection so the X1's CPU stays free for the LLM.
4. Automations: "Person at the front door" spoken through the house + snapshot to your phone; camera feed pops up on the wall dashboard.

### Tier 4 — Hands: the house acts physically (~$100–300, as budget allows)

| Item | Cost | What it unlocks |
|---|---|---|
| **Smart lock** (Zigbee/Z-Wave, e.g. Schlage/Aqara) | ~$120–200 | "Jarvis, secure the house"; auto-lock when everyone leaves; auto-unlock on arrival |
| **Smart plugs w/ power monitoring** (4-pack) | ~$30 | "Did I leave the curling iron on?"; phantom-load tracking; turn anything dumb into smart |
| **Motorized blinds/curtains** (SwitchBot/Zemismart) | ~$70+ ea | Blinds open with sunrise — maximum Stark Tower |
| **Smart thermostat** (if you have HVAC control) | ~$70+ | Pre-heat/cool based on presence and schedule |
| **Whole-house announcements** | ~$0–20 | Any speaker + the X1, or used Nest Minis/Echo flashed or media-player integrated |

### Tier 5 — The Jarvis personality (free, pure software)

- Give the LLM a **system prompt** in the Ollama/Claude integration: "You are Jarvis, a dry-witted British AI butler..." — it will genuinely stay in character.
- **Morning briefing** automation: at your wake time, Jarvis speaks — weather, calendar, commute, "the vacuum completed its run, no intruders detected overnight."
- **Contextual sass**: automations that trigger LLM-generated speech ("Welcome home. You left the bedroom light on for 9 hours. Again.")
- **Security mode**: one command arms everything — cameras alert on any person, lights simulate occupancy while away, doors report every event.

### Budget summary

| Milestone | Total spend |
|---|---|
| Talking Jarvis in 2 rooms (Tier 1) | ~$120 |
| + house-wide sensing (Tier 2) | ~$250 |
| + AI cameras (Tier 3) | ~$400 |
| + locks/blinds/plugs (Tier 4) | ~$600–700 |

Spread over months, and every dollar keeps working — no subscriptions, ever.

---

## Phase 14 — Outside-the-box ideas

### The X1 itself is secret hardware — ALL BUILT
- **Built-in UPS** ✔: outage/restore/low-battery alerts from the laptop's own AC + battery sensors (`ha-config/automations/power.yaml`).
- **Screen** ✔: Magic Mirror-style always-on hub display (`bootstrap/setup-kiosk.sh`).
- **Webcam + mic** ✔: `bootstrap/setup-satellite.sh` makes the webcam an RTSP camera (Frigate/HA-ready) and the mic a "hey Jarvis" satellite.

### The vacuum is a robot, not just a vacuum
- **Patrol mode**: away-mode automation sends the Juno roaming as a mobile deterrent/noise-maker.
- **Messenger bot**: manual-drive it to another room with a note or snack on top.
- **Clutter detector**: "vacuum stuck" events become "someone left stuff in the hallway" alerts.

### The house as a living thing
- **Vibe engine**: lights shift with music (beat-sync), weather (storm = blue-grey), or calendar (red glow 5 min before a meeting).
- **Wellness AI**: nightly LLM "house report" — arrival times, odd door events, energy trends.
- **Occupancy theater**: on vacation, replay your *actual* recorded light-usage history so the house looks genuinely lived-in.

### Weird sensors, big wins
- **Vibration sensor (~$12) on washer/dryer** → "laundry's done" announcement.
- **NFC stickers (~$0.30 ea)**: tap phone on nightstand = bedtime mode; door frame = leaving mode.
- **Face recognition** (Double Take + Frigate): greet people by name; unknown face = photo to your phone.
- **CO2 sensor**: stale-air alerts → auto fan; better sleep and focus.
- **Wellness check** for a relative: no motion by 10am → text you.

## Phase 15 — Fridge & pantry inventory ("Jarvis, we're out of milk")

Goal: the house tracks what food you have, reminds you what's low or expiring, and builds your shopping list — even if you tell it by voice.

### Level 1 — Voice-driven shopping list (free, 10 minutes)
1. HA has a built-in **Shopping list / To-do list** integration (Settings → Add Integration → "Local to-do").
2. Assist understands it natively: say **"add milk to the shopping list"** from any voice satellite or your phone.
3. Put the list card on your dashboard and phone; check items off at the store.
4. Automation: every Sunday 9am, Jarvis reads the list aloud / sends it to your phone.

### Level 2 — Real inventory with Grocy (free, a weekend)
1. Run **Grocy** (self-hosted grocery ERP) in Docker on the X1:
   ```bash
   docker run -d --restart=unless-stopped -p 9283:80 \
     -v /opt/grocy:/config linuxserver/grocy
   ```
2. Add the **Grocy integration** to HA (via HACS). Now you have: stock levels per product, expiry dates, min-stock thresholds, and a "what's below minimum" auto-shopping-list.
3. **Barcode scanning**: the free **Grocy companion app** (Android) turns your phone into a scanner — beep items in when unloading groceries, beep out when you finish them. ~5 seconds per item.
4. Automations:
   - Anything drops below its minimum → auto-added to the shopping list + phone notification.
   - **"Expiring soon" morning briefing**: "the chicken expires tomorrow, yogurt in 3 days."
   - Voice: "Jarvis, how much milk do we have?" / "Jarvis, we finished the eggs" (custom sentences mapped to Grocy consume/add).

### Level 3 — LLM + ordering (the fun part)
- Give the LLM access to the Grocy inventory: **"Jarvis, what can I make for dinner?"** → it answers from what you actually have.
- **Ordering**: fully-automatic grocery ordering APIs are rare in the US, so best options:
  - Shopping list auto-formatted and sent to your phone as a tap-to-open **Walmart/Instacart/Amazon Fresh search links** per item — 90% of the effort gone.
  - Amazon-stocked staples can reorder via **Alexa-less Amazon reorder links** or subscribe-and-save.
- Stretch goal: a small **fridge-door camera** + LLM vision snapshot each time the door closes ("looks like you're low on greens") — experimental but people do it.

### Fridge tracking honesty check
No system magically knows fridge contents — even Samsung's $3,000 AI fridge gets it wrong. The winning combo is: barcode-scan on unload (fast), voice "we're out of X" in the moment, and min-stock auto-lists. That's genuinely better than Alexa's shopping list because it tracks *quantities and expiry*, not just names.

---

## Phase 16 — Fire TV integration

Fire TV is Android under the hood, so Home Assistant controls it via ADB (no Alexa needed).

### Setup (~15 min)
1. On the Fire TV: **Settings → My Fire TV → Developer Options → enable ADB Debugging**. (If Developer Options is hidden: Settings → My Fire TV → About → click the device name 7 times.)
2. Give the Fire TV a static IP (DHCP reservation on your router, same as the X1).
3. In HA: **Settings → Add Integration → "Android TV"** (Android Debug Bridge), enter the Fire TV's IP, and accept the pairing prompt on the TV.

### What you get
- **Full media entity**: play/pause/stop, volume, current app shown on your dashboard.
- **Launch apps by voice**: "Jarvis, put on Netflix" (each app has an ADB launch command; Netflix, Prime, YouTube, Disney+, etc. all work).
- **Movie mode automation**: Fire TV starts playing → lights dim to 20%; paused → lights up a bit; stopped → lights back to normal.
- **Send any remote keypress** via ADB (up/down/select/home), so automations can do anything the remote can.
- **Notifications ON the TV**: install the free "Notifications for Fire TV" app on the Fire TV and add the HA `nfandroidtv` integration — pop-ups on screen like "someone's at the front door" (with the camera snapshot, once Phase 13 Tier 3 cameras exist).
- **Sleep timer that actually works**: no activity after 1am → pause playback, turn off TV, lights off.
- Presence-ish bonus: "Fire TV is playing" is a great signal for room-occupancy automations.

### Voice examples once wired up
- "Jarvis, movie night" → Fire TV on, Netflix launched, lights to 15% warm, phone on DND.
- "Jarvis, pause the TV" from any room.
- Doorbell/camera event while watching → picture-in-picture style notification on the TV instead of missing it.

---

## Phase 17 — Your computers join the system

Every PC/laptop becomes a device Jarvis can see, wake, control, and use as a presence signal.

### Power control (free, ~15 min per machine)
1. **Wake-on-LAN**: enable "Wake on LAN" in each computer's BIOS/UEFI (and in Windows: Device Manager → network adapter → Power Management). Works on wired Ethernet; most WiFi cards don't support it.
2. In HA: add the **Wake on LAN** integration with the computer's MAC address → "Jarvis, turn on my PC."
3. Give each computer a static IP/DHCP reservation like everything else.

### Windows machines — HASS.Agent (free)
1. Install **HASS.Agent** on each Windows PC and point it at the X1's HA URL with a long-lived access token (created in your HA profile page).
2. You get:
   - **Sensors**: CPU/RAM/GPU load, active window, session state (locked/unlocked), webcam/mic in use, battery.
   - **Commands**: shutdown, restart, sleep, lock, launch any app, media keys, volume, send keystrokes.
   - **Desktop notifications**: HA alerts pop up on the PC screen — "vacuum stuck", "someone at the door" while you work.

### Mac / Linux machines
- **System Bridge** (free, cross-platform) gives similar sensors + commands, or
- Simple **SSH commands from HA** (shell_command integration): sleep, shutdown, run scripts. For Mac sleep: `ssh user@mac pmset sleepnow`.

### What this unlocks
- **"At my desk" presence**: PC unlocked + active = you're in the office → office lights on, DND mode, no vacuum in that room.
- **Gaming mode**: GPU load spikes → lights go RGB, notifications muted.
- **Energy saver**: PC idle 30+ min and you've left the house → auto-sleep it.
- **"Jarvis, lock all my computers"** — one command, every machine locks (great panic/leaving button).
- **Morning boot**: alarm goes off → PC wakes, so it's ready by the time you sit down.
- Dashboard tile per computer: on/off, load, uptime, who's logged in.

---

## Phase 18 — LifeOS: Budget & Vault + Body Ops

A custom app in the same stack (its own container, port 8090, mobile-friendly web UI)
— things no HA dashboard can do.

### Body Ops — fitness & fuel
- **Meal suggestions** in 5- and 15-minute tiers, matched to pantry staples; defaults
  prefer sweet potatoes and avoid rice/bread/mashed potatoes.
- **One-tap overrides**: "Sometimes / Today" on any suggestion card — the treat is
  recorded without changing your defaults, and a pragmatic (never shaming) nudge follows:
  smaller portion, swap a later meal, or a 15-min workout.
- **Protein tracking**: hard daily target (default 100 g), progress bar, high-protein
  snack suggestions when a shortfall is detected.
- **Weigh-ins, steps, vitamins**: trends, streaks, encouraging-on-loss / pragmatic-on-gain
  messages.
- **Apple Watch / smart scale**: the iPhone "Health Auto Export" app posts steps and
  weigh-ins to `POST /api/webhooks/health` automatically.

### Budget & Vault — the finance spine
- Tracks **True Lion, OnePay, FreePlay, Relay**: balances, deposits, bills with due days.
- **Plan view**: lines deposits against unpaid bills, recommends what to pay now vs hold,
  shows the leftover after bills.
- **Food ↔ money linkage**: confirmed food overrides surface as nudges in the weekly
  money plan, so treats and budget adjust together.
- **Vaultborne separation**: Vaultborne-flagged accounts stay separate from discretionary
  flows — never mixed into treat/override money.
- Manual quick-entry to start (30 seconds a day); aggregator/API automation is a later
  investigation.

### LifeOS v0.2 — the personal operating system layer (built)
- **Morning briefing** (`GET /api/briefing`): weather (Open-Meteo, set `LIFEOS_LAT`/
  `LIFEOS_LON`), protein/steps status, vitamins, bills due in 7 days, leftover after
  bills, today's workouts, breakfast pick — plus a `speech` string Jarvis reads aloud
  at 7:30 (see `ha-config/automations/briefing.yaml`).
- **Weekly review** (`GET /api/review/weekly`): weight delta, protein/step averages,
  money in vs bills paid, treats vs workouts, streaks — announced Sundays at 6pm.
- **Workout scheduler**: plan sessions in the UI; logging a treat auto-schedules a
  15-min balance-the-treat circuit for the same day.
- **Grocy pantry sync**: set `GROCY_URL` + `GROCY_API_KEY` and hit "Sync from Grocy";
  grocery suggestions are generated from your 7-day protein deficit.
- **Multi-profile**: per-person protein/step/calorie targets; switch the active profile
  from the header dropdown — all logs (meals, steps, weigh-ins, vitamins, workouts)
  are profile-scoped.
- **Photo meal logging** (`POST /api/body/meals/photo`): plate photos are saved to the
  data volume; auto macro estimation plugs in once a vision model (Ollama llava) is
  running — manual macros until then.

### House-side additions (config stubs until hardware lands)
- **Sleep intelligence** (`automations/sleep_intelligence.yaml`): phone charging + no
  motion after 11pm → goodnight scene; lights fade in 15 min before your phone alarm.
- **Guest mode** (`scripts/guest_mode.yaml` + `input_boolean.guest_mode`): pauses
  personal announcements while friends are over.
- **Leaving / panic button** (`scripts/panic_leaving.yaml`): one tap (or NFC sticker) —
  lights off, TV off, vacuum home, computers locked (once HASS.Agent is in).
- **Energy tracking** (`automations/energy.yaml`): needs power-monitoring smart plugs;
  "PC still on and nobody home" alert with a sleep-it button.
- **Mailbox watch** (`automations/mailbox.yaml`): vibration sensor or camera stub.
- **Car integration**: add your car's HA integration when known (Ford/Tesla/Hyundai…);
  its fuel/charge sensors feed the briefing.

---

## Phase 19 — Jarvis context, policy, and Command Center

This is the cognitive spine that turns the collection of services into one system.

### Implemented now

- Durable event, fact, proposal, and action-audit storage in the LifeOS database.
- Home Assistant state changes projected into occupancy, perimeter, alarm, hazard,
  house-mode, and X1 hardware context.
- A fused command payload combining house state with Body Ops, Vault Flow, daily
  priorities, capability readiness, policies, and pending proposals.
- Explicit policies for every action: scope, risk, reversibility, confirmation rule,
  and remote-execution permission.
- Three deterministic behaviors: arrival orchestration, last-person departure anomaly
  detection, and nightly security review.
- A side-effect-free Behavior Lab for all three scenarios. Simulation never writes
  house facts, creates proposals, or calls Home Assistant.
- A privacy-minimized local perception path: the X1 vision worker reports camera
  health, hand presence, and gesture metadata into context without transmitting or
  retaining raw frames. Identity recognition and gesture authorization are disabled.
- A fourth read-only Behavior Lab scenario for perception, including explicit proof
  that no house action is executed.
- Proposal dismissal and action audit history.
- A full-screen X1-first Command Center with responsive phone operation.

### Authority model

1. Sensors and LifeOS data describe reality.
2. Deterministic behavior code produces a proposed action and reason.
3. The policy registry decides whether automatic, remote-confirmed, or local visual
   confirmation is required.
4. The UI and voice layer explain the proposal; they do not bypass policy.
5. Execution and dismissal are written to the audit log.

An LLM can summarize, converse, and recommend. It must never be the component that
decides whether a physical action is authorized.

## Phase 20 — Capability expansion roadmap

Build these in evidence-driven order rather than adding disconnected gadgets.

### Reliability and observability

- Add authenticated API access for LifeOS and rotate Home Assistant tokens.
- Add service health, database-size, backup-age, event-lag, microphone-level, camera
  frame-age, and disk-wear telemetry.
- Add proposal rate limits, retention policies, exportable audit history, and restore
  drills—not just backup creation.
- Add offline/degraded modes so every screen says what still works when HA, the LLM,
  internet, or a sensor is unavailable.

### Context quality

- Fuse phone GPS, router presence, Bluetooth proximity, motion/mmWave, door events,
  calendar, weather, and device activity with confidence and freshness metadata.
- Add room-level occupancy and contradiction detection instead of trusting a single
  presence source.
- Record causal links and correlation IDs so Jarvis can answer “why did you do that?”
  from the event and action trail.

### LifeOS intelligence

- Turn daily health, meals, workouts, bills, spending, groceries, and routines into a
  ranked daily plan with dismiss/snooze/complete states.
- Add calendar/time-block integration and an inbox for voice-captured tasks.
- Add local semantic memory with explicit review, editing, expiry, and privacy controls.
- Add explainable weekly trend detection; recommendations must cite the underlying
  measurements and never present medical or financial guesses as facts.

### Voice and multimodal operation

- Migrate the deprecated Wyoming host satellite when Linux Voice Assistant becomes
  stable enough for this hardware.
- Add barge-in, echo cancellation, microphone health, speaker routing, visual listening
  state, and a physical privacy/mute indicator.
- Use rear/front cameras for opt-in gesture and occupancy signals. Face identity and
  recordings remain disabled until explicit retention and consent rules exist. Basic
  hand-presence telemetry is implemented; full person/room occupancy still requires
  sensor fusion and must not be inferred from a missing hand.

### Security and safety

- Add per-user roles, local-presence confirmation, tamper alerts, secrets isolation,
  authenticated webhooks, and encrypted off-device backups.
- Keep unlock, disarm, garage movement, purchases, and computer-control actions behind
  narrow, testable policies. Presence alone is never proof of identity.
- Run every new behavior against recorded/synthetic scenarios before enabling live mode.

### UX completion criteria

- Every state has loading, empty, stale, degraded, offline, and recovery treatments.
- Every proposal shows what triggered it, what will happen, risk, confirmation needs,
  and how to cancel it.
- Tablet interactions work from arm's length; phone interactions work one-handed;
  keyboard and screen-reader operation remain complete.
- The Command Center must answer four questions in under five seconds: What is happening?
  What needs me? Why? What is Jarvis allowed to do?

---

## Suggested build order

1. Deploy and verify the core stack, backups, and remote access.
2. Commission X1 audio, cameras, Bluetooth, touch, power, and thermal telemetry.
3. Pair presence and perimeter sensors before enabling security proposals.
4. Connect lights, vacuum, TV, and speakers; replace every placeholder entity ID.
5. Exercise arrival, departure, and nightly scenarios in the Behavior Lab.
6. Enable live low-risk actions first, observe the audit trail, then graduate confirmed
   high-risk actions one at a time.
7. Add optional media, pantry, camera AI, and LLM services only after the core remains
   stable through reboots and network loss.

---

## Phase 21 — Sanctuary OS v1.2 completion

Sanctuary v1.2 replaces disconnected placeholder routines with one calibrated,
explainable apartment state machine.

- Home Assistant is the authoritative local control plane; LifeOS owns context,
  proposals, policy, and audit; Google Home is optional.
- The apartment floor plan is the primary room interface and Bedroom #2 is the
  Office. `Dining Area` is the canonical display name.
- All room lighting resolves through Home Assistant Areas and the `light` domain.
  Missing devices are readiness facts, not service-call failures.
- Scheduled routines are disabled until real-room calibration passes. Manual
  modes, Manual Hold, Resume, and calibration tests remain available.
- Weekday Sunrise, presence-driven Welcome/Away, Shower, Wind Down,
  Thunderstorm-until-Sunrise, and Sunday Cleaning share one priority model.
- Home Assistant browser sessions exchange for short-lived HttpOnly LifeOS
  sessions, eliminating the embedded token prompt without putting long-lived
  secrets in URLs or browser storage.
- Synthetic Sanctuary scenarios cover every mode, priority gates, recording and
  guest protection, unavailable devices, and lighting-only safety boundaries.

Commissioning and rollback are defined in
[SANCTUARY-COMMISSIONING.md](SANCTUARY-COMMISSIONING.md).

---

## Reality check vs Alexa/Google

| | Your X1 hub | Alexa/Google |
|---|---|---|
| Privacy | Fully local, no data leaves home | Cloud, data harvested |
| Works offline | Yes | Mostly no |
| Automation power | Unlimited | Very limited |
| Voice accuracy | Good (Whisper) | Slightly better |
| Setup effort | A weekend | 10 minutes |
| Cost | $0 (hardware you own) | Locked to ecosystem |
