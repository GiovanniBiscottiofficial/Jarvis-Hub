# Jarvis Hub

Self-hosted smart home hub for a ThinkPad X1 (Gen 3) — better than Alexa/Google Home:
fully local, private, conversational (LLM-powered), and infinitely automatable.

Controls: Monster Illuminessence lights, iHome AutoVac Juno (iHRV9), Fire TV, your computers, and anything else you add later.

Full long-form plan: [docs/PLAN.md](docs/PLAN.md)

## Quick start (on the X1, fresh Ubuntu Server 24.04)

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/GiovanniBiscottiofficial/Jarvis-Hub.git
cd Jarvis-Hub
bash bootstrap/setup-x1.sh
```

Then open `http://<laptop-ip>:8123`, create your account, and follow the phases below.

## What's in this repo

| Path | What it is |
|---|---|
| `docker-compose.yml` | The whole stack: Home Assistant + optional voice / Grocy / LLM profiles |
| `bootstrap/setup-x1.sh` | One-time laptop prep (lid-close, no-sleep, Docker) + starts the stack |
| `bootstrap/setup-kiosk.sh` | Turns the X1's screen into a Google-Home-style hub display (boots into the Jarvis dashboard fullscreen) |
| `bootstrap/setup-remote-access.sh` | Tailscale tunnel — control everything from work/cellular, nothing exposed to the internet |
| `bootstrap/setup-backups.sh` | Nightly 3 AM backup of the LifeOS DB + HA config (`backup.sh` runs one on demand; point `BACKUP_DIR` at a USB drive) |
| `bootstrap/setup-satellite.sh` | Turns the X1's own mic into a "hey Jarvis" satellite and its webcam into an RTSP camera for Frigate/HA |
| `ha-config/configuration.yaml` | Home Assistant base config (loads the automations/scripts below) |
| `ha-config/automations/` | Starter automations: movie mode, presence, vacuum-stuck alert, shopping reminder |
| `ha-config/scripts/` | Voice-callable scenes: "movie night", "goodnight" |
| `ha-config/dashboards/jarvis.yaml` | "Jarvis" command-center dashboard (Home / Vacuum / Media / Kitchen / System tabs) |
| `lifeos/` | LifeOS app: Vault Flow (finance) + Body Ops (health/fuel) — its own container on port 8090 |
| `docs/PLAN.md` | The full build plan |

The automation/script files contain placeholder entity IDs (`light.living_room`,
`vacuum.juno`, `media_player.fire_tv`, `notify.mobile_app_phone`) — rename them to match
your real devices after connecting them (Settings → Devices & Services → Entities).

## Phase checklist

- [ ] **Phase 0–1**: run `bootstrap/setup-x1.sh`, open HA at `:8123`, create account
- [ ] **Phase 2a — Lights**: re-pair Monster lights in the *Smart Life* app, then HA → Add Integration → **Tuya** (scan QR with Smart Life)
- [ ] **Phase 2b — Vacuum**: try pairing the Juno in Smart Life too; if it won't, install HACS + **tuya-local**
- [ ] **Phase 3 — Voice**: `docker compose --profile voice up -d`, then HA → Add Integration → **Wyoming** three times (ports 10300 Whisper, 10200 Piper, 10400 openWakeWord); build the Assist pipeline with wake word "hey Jarvis"
- [ ] **Phase 5 — Presence**: install the HA Companion app on every phone; link people in Settings → People
- [ ] **Phase 8 — Conversation LLM**: `docker compose --profile llm up -d && docker exec -it ollama ollama pull llama3.2:3b`, add the **Ollama** integration (`http://<laptop-ip>:11434`), set it as the Assist conversation agent, enable "Control Home Assistant". (Or use the Anthropic/OpenAI integration for a smarter cloud agent.)
- [ ] **Phase 15 — Fridge/pantry**: `docker compose --profile grocy up -d`, open `http://<laptop-ip>:9283`, add the Grocy integration via HACS; barcode-scan with the Grocy Android app
- [ ] **Phase 16 — Fire TV**: enable ADB Debugging on the Fire TV, HA → Add Integration → **Android TV** with its IP; install "Notifications for Fire TV" for on-screen alerts
- [ ] **Cameras (when you get them)**: put each camera's RTSP URL in `frigate/config.yml`, run `docker compose --profile cameras up -d`, add the **Frigate** integration via HACS — live views land on the dashboard's Cameras tab (viewable from work via Tailscale)
- [ ] **Jarvis's personality**: once the LLM agent is set up (Phase 8), paste [docs/jarvis-personality.txt](docs/jarvis-personality.txt) into the agent's Instructions field so he actually sounds like Jarvis
- [ ] **Phase 17 — Computers**: enable Wake-on-LAN in BIOS, add MACs to the `wake_on_lan` integration; install **HASS.Agent** on Windows machines
- [ ] **Phase 18 — LifeOS**: comes up with the core stack at `http://<laptop-ip>:8090` — Today tab (morning briefing, meal cards with Sometimes/Today overrides, protein/steps bars, vitamin streaks), Body Ops (weigh-ins, quick meal log, workout planner, photo meal logging, pantry + grocery suggestions), Vault Flow (accounts, deposits vs bills, payment recommendations, leftover, food-money nudges), Review tab (weekly review + household profiles). Auto-sync Apple Watch/scale by pointing the iPhone "Health Auto Export" app at `http://<laptop-ip>:8090/api/webhooks/health`. Optional: set `LIFEOS_LAT`/`LIFEOS_LON` in `.env` for weather in the briefing, and `GROCY_URL`/`GROCY_API_KEY` for pantry sync. Jarvis reads the briefing at 7:30 and the weekly review on Sundays (`ha-config/automations/briefing.yaml`)

Everything else (dashboards, cameras, Zigbee sensors, custom wake word, the whole Jarvis roadmap) is in [docs/PLAN.md](docs/PLAN.md).

## Ask Jarvis about your life

Once the voice pipeline (Phase 3) is up, these work out of the box — by voice or typed
into Assist (works remotely through the Companion app too):

Ask:
- "brief me" · "how did I do this week"
- "how much protein do I have left" · "how's my steps"
- "what bills are due this week" · "how much money do I have left"
- "what can I eat" · "what's for dinner" — pantry-matched picks plus how much
  protein you still need today
- "evening report" · "how did I do today" — the day's recap: protein, water,
  steps, workouts, vitamins, spending, and upcoming bills
- "how much have I spent this week" · "what's on the grocery list"
- "how much water have I had today" · "water check"
- "how are my savings" · "how much have I saved"
- "is the house ready for the night" · "house status" — Jarvis checks the
  lights, vacuum, and house modes and reports back

Log (Jarvis writes straight into LifeOS):
- "I took my vitamins" · "log 45 grams of protein"
- "log a weigh-in of 185" · "log 6000 steps"
- "I'm having a treat" — schedules the 15-min balance circuit automatically
- "I did my workout" · "log a workout" — completes today's planned session
  (or logs a freestyle one) so streaks stay honest
- "I spent 40 at the gas station" — discretionary spending (shows up in the
  briefing and weekly review)
- "add chicken to the grocery list" · "we're out of milk"
- "take chicken off the grocery list" · "clear the grocery list"
- "remind me to move the laundry in 30 minutes"
- "set a timer for 10 minutes" · "cancel my timers" / "cancel all my reminders"
- "log a glass of water" · "I drank 3 glasses of water" — counts toward the
  daily 8-glass target (shown on the LifeOS Today tab)
- "set my usual breakfast to sweet potato and eggs", then "log my usual
  breakfast" — one-phrase meal logging with real macros

Vault Flow by voice:
- "log a deposit of 500 to OnePay" · "add 200 to True Lion"
- "mark rent paid" · "I paid the electric bill"
- "add a bill called internet for 80 dollars due on the 15th"
- "add an account called savings"
- "add 50 to my vacation fund" · "save 25 dollars for Christmas" — savings
  goals with progress bars on the Vault Flow tab (a new goal is created
  automatically the first time you mention it)

House control:
- "tell everyone dinner's ready" / "announce ..." — whole-house announcement
  (edit the speaker in `ha-config/scripts/announce.yaml`)
- "wake up" · "initialize all systems" — the full Jarvis awakening sequence
  (lights, greeting, status report to your phone)
- "quiet mode on" / "quiet mode off" — hold all announcements except
  emergencies (also flips on automatically while the TV is playing)
- "I'm having a bad day" · "I'm stressed" — cozy lights + a kind word
- "thank you Jarvis" — he has opinions about his own brilliance

### Beyond the ordinary (the proactive layer)

All in `ha-config/automations/` — active immediately unless noted:

- **Proactive**: workday wake-up briefing at 6:30, dinner suggestion at 4 PM
  (`proactive.yaml`).
- **Contextual**: quiet mode auto-on during movies via the `Current activity`
  sensor; dim red night-path light on late-night motion once a motion sensor
  is added (`contextual.yaml`, sensor in `configuration.yaml`).
- **Personality**: random witty confirmations (`scripts/jarvis_quips.yaml`),
  the full awakening sequence (`scripts/awaken.yaml`), mood support.
- **Entertainment**: upgraded movie night (Fire TV + dimmed ambiance), lights
  fade back up after the credits; Spotify mood-music scaffold commented until
  you add the Spotify integration (`entertainment.yaml`).
- **Digital twin**: new **Twin** dashboard tab — live activity, house modes,
  power flow; drop `ha-config/www/floorplan.png` and uncomment the
  picture-elements block for a tappable floorplan.
- **Needs hardware/integrations (safe to leave as-is until then)**:
  - Emergency response — all lights red + evacuation announce + phone alert on
    smoke/CO/leak (`emergency.yaml`; any Zigbee detector, leak sensors ~$12).
  - Security — known-face welcome, unknown-visitor snapshot with actionable
    phone notification, wave-at-camera gesture (`security.yaml`; needs
    Frigate + Double Take on the camera; auto-unlock stays commented on purpose).
  - Environment — storm prep + air-quality purifier automation
    (`environment.yaml`; needs a weather integration / PM2.5 sensor).
  - Optimization — charge-when-power-is-cheap, HVAC filter reminder
    (`optimization.yaml`; needs a price sensor / smart thermostat).
  - Kitchen — 5 PM "use it before it expires" from Grocy (`kitchen.yaml`).
  - Wellness — morning sleep recap from your phone's sleep data
    (`wellness.yaml`; Companion app sleep sensors).

Jarvis also announces new LifeOS nudges through the house speaker and your phone
(`ha-config/automations/nudges.yaml` — set your real speaker/notify entities there;
nudges stay quiet during guest mode and sleep mode).

Sentences live in `ha-config/custom_sentences/en/lifeos.yaml`, answers in
`ha-config/intents.yaml` (fed by LifeOS's `/api/ask`). Add your own phrasings any time.

## LifeOS on your phone — a real app

LifeOS is an installable PWA: open `http://<hub-ip>:8090` on your phone, then
**Add to Home Screen** (Safari share menu on iOS, Chrome menu on Android). You
get a proper Jarvis-blue app icon and a fullscreen standalone app — no browser
bars — and the app shell still opens if the hub is briefly unreachable.

## The hub display (Google-Home style)

The X1 isn't just the server — its own screen becomes the house's control panel:

```bash
bash bootstrap/setup-kiosk.sh   # then reboot
```

It boots into a live animated **Jarvis boot splash** (spinning arc-reactor core,
"INITIALIZING SYSTEMS…", moving circuit grid — `ha-config/www/jarvis-splash.html`,
also viewable any time at `http://<hub-ip>:8123/local/jarvis-splash.html`), which
hands over to the **Wall** view of the Jarvis dashboard — big clock, the
morning briefing, giant light/scene/vacuum tiles, the shopping list, and a "Jarvis
activity" feed of his recent actions — fullscreen,
screen always on, in the dark-glass **Jarvis** theme (`ha-config/themes/jarvis.yaml`;
set it everywhere from your HA profile → Theme → Jarvis). No keyboard needed: the
X1's screen logs into HA automatically (localhost is a trusted network) and an
on-screen touch keyboard pops up whenever you tap a text box.
Prefer another page? `KIOSK_URL=http://localhost:8123/jarvis-hub/home`
(full dashboard) or `KIOSK_URL=http://localhost:8090` (LifeOS) before running the script.
(Ctrl+Alt+F2 gets you back to a terminal any time.)

## House modes & taps

- **Vacation mode** (toggle on the Home tab): occupancy theater — staggered,
  randomized evening lights so the house looks lived-in (`ha-config/automations/vacation.yaml`).
- **NFC tags** (~$0.30 stickers): tap the nightstand tag for goodnight, the door-frame
  tag for leaving. Write tags with the Companion app (Settings → Tags), then paste the
  tag IDs into `ha-config/automations/nfc_tags.yaml`.
- **Backups**: `bash bootstrap/setup-backups.sh` installs a nightly 3 AM backup of the
  LifeOS database and HA config to `~/jarvis-backups` (or `BACKUP_DIR=/media/usb ...`).
- **Built-in UPS**: the X1's battery keeps the hub alive through outages, and it
  notices — a power cut pings your phone (with battery %), Jarvis announces it, and
  you get a "power restored" + low-battery warning too (`ha-config/automations/power.yaml`).

## The X1 as a sensor node

The laptop's own mic and webcam become house hardware:

```bash
bash bootstrap/setup-satellite.sh
```

- **Mic → "hey Jarvis" satellite**: after the script, add the **Wyoming** integration
  once more (host `localhost`, port **10700**), assign your Jarvis pipeline to the new
  satellite, and the laptop listens for "hey Jarvis" room-wide — answers come out its
  speakers.
- **Webcam → camera**: a local stream at `rtsp://<laptop-ip>:8556/x1_webcam`. Quick
  view: HA → Add Integration → **Generic Camera** with that URL. Person detection:
  uncomment the `x1_webcam` block in `frigate/config.yml` and run
  `docker compose --profile cameras up -d`.

## Access from anywhere

On your wifi, every device already gets the full platform — HA at `:8123`, LifeOS at
`:8090`. To log in from work or cellular (check cameras, flip lights, ask Jarvis to do
things through the Companion app's Assist):

```bash
bash bootstrap/setup-remote-access.sh
```

That sets up Tailscale (free): a private encrypted tunnel between your phone and the
hub. Nothing is opened to the public internet, and it works from any network.

## Useful commands

```bash
docker compose logs -f homeassistant     # watch HA logs
docker compose pull && docker compose up -d   # update everything
docker compose --profile voice --profile grocy --profile llm up -d  # run it all
```
