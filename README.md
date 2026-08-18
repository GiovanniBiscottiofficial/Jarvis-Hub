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
- [ ] **Phase 17 — Computers**: enable Wake-on-LAN in BIOS, add MACs to the `wake_on_lan` integration; install **HASS.Agent** on Windows machines
- [ ] **Phase 18 — LifeOS**: comes up with the core stack at `http://<laptop-ip>:8090` — Today tab (morning briefing, meal cards with Sometimes/Today overrides, protein/steps bars, vitamin streaks), Body Ops (weigh-ins, quick meal log, workout planner, photo meal logging, pantry + grocery suggestions), Vault Flow (accounts, deposits vs bills, payment recommendations, leftover, food-money nudges), Review tab (weekly review + household profiles). Auto-sync Apple Watch/scale by pointing the iPhone "Health Auto Export" app at `http://<laptop-ip>:8090/api/webhooks/health`. Optional: set `LIFEOS_LAT`/`LIFEOS_LON` in `.env` for weather in the briefing, and `GROCY_URL`/`GROCY_API_KEY` for pantry sync. Jarvis reads the briefing at 7:30 and the weekly review on Sundays (`ha-config/automations/briefing.yaml`)

Everything else (dashboards, cameras, Zigbee sensors, custom wake word, the whole Jarvis roadmap) is in [docs/PLAN.md](docs/PLAN.md).

## Useful commands

```bash
docker compose logs -f homeassistant     # watch HA logs
docker compose pull && docker compose up -d   # update everything
docker compose --profile voice --profile grocy --profile llm up -d  # run it all
```
