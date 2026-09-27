# AI Stock Simulator

An agent-based market simulator where a society of **LLM-driven investors** trades four fictional AI companies, argues on a virtual forum, and reflects on its own decisions — all watched from a live web dashboard.

> ⚠️ This is a toy / research playground, not a trading tool.
> All companies, tickers, prices and "news" are fictional. Nothing here is financial advice.

---

## What it does

- **Virtual clock** — one simulated day = 27 market-open ticks + 2 market-closed ticks (`main_loop.VirtualClock`). Each tick sleeps ~1 second, so a "day" passes quickly.
- **Market rounds** — every active agent receives a prompt (personality, memory, portfolio, market context, fundamentals) and returns a JSON trade list. `market_engine.py` matches the orders and moves prices.
- **Social rounds** — agents read the virtual forum feed and decide to post, reply, or stay silent. Their opinions feed back into sentiment.
- **Day-close settlement** — salary paid minus living cost, central policy event, agent **reflection round** (belief / episodic / relationship / fundamental memory), fundamental events (earnings beat & miss, guidance, dividends), automatic stock splits of overpriced tickers, DB health check and a daily audit log.
- **Persistence** — everything lives in a SQLite database (`server_society.db`) plus a few Markdown logs.
- **Web dashboard** — Flask app with live status, daily & intraday candles, wealth leaderboard, agent detail views, trade feed, forum feed, policy/bulletin history, and forms to create agents or post bulletins.

## Repository layout

| Path | Purpose |
| --- | --- |
| `simulation_server.py` | Main simulation loop, world setup, rounds, settlement |
| `market_engine.py` | Order matching and price dynamics |
| `forum_system.py` | Virtual forum (posts, replies, hot posts) |
| `llm_router.py` | LLM abstraction: prompt modes, JSON validation, retries, fallbacks |
| `database.py` | SQLite schema and queries |
| `main_loop.py` | Virtual clock / market state |
| `stock_split_service.py`, `stock_split.py` | Stock split rules and CLI helper |
| `web_monitor.py` | Flask dashboard (`http://127.0.0.1:5000`) |
| `templates/index.html` | Dashboard front-end |
| `db_health_check.py`, `monitor_market.py` | Small diagnostics tools |
| `agents/` | One JSON file per agent (**ships empty — create your own cast**) |
| `test_*.py`, `mvp_test.py` | Manual test scripts |
| `start_ai_stock_simulator.bat`, `stop_ai_stock_simulator.bat` | Windows helpers |

---

## Requirements

- **Python 3.10+** (developed on 3.14)
- **git** and a shell — bash / zsh, PowerShell or cmd
- An LLM: **Xiaomi MiMo** (OpenAI-compatible) *or* **Google Gemini** *or* a **local llama.cpp** model (no key required)

The two `.bat` helpers are Windows-only; every other command below runs on macOS, Linux and Windows alike.

## Install

### 1. Get the code

```bash
git clone https://github.com/42331game-star/ai_stock_simulator.git
cd ai_stock_simulator
```

### 2. Install Python 3.10+

```bash
# macOS (Homebrew)
brew install python3

# Debian / Ubuntu
sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip git
```

```powershell
# Windows — winget, or download the installer from https://www.python.org/downloads/
winget install Python.Python.3.12
```

Sanity check: `python3 --version` in bash, `py -3 --version` on Windows.

### 3. Virtual environment + dependencies

```bash
# macOS / Linux (bash or zsh)
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

```powershell
# Windows PowerShell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

```bat
:: Windows cmd — use this if the PowerShell execution policy blocks activation
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
```

Verify everything imported:

```bash
python -c "import flask, google.generativeai; print('dependencies OK')"
```

> Every **new** shell starts outside the venv — run `source .venv/bin/activate`
> (or `.venv\Scripts\activate.bat`) again before the `python ...` commands below.
> `start_ai_stock_simulator.bat` finds Python on its own and does not need the venv.

### 4. Configure your LLM — next section

## Configure your LLM (never hardcode keys)

The simulator reads configuration from **environment variables only**:

| Variable | Meaning |
| --- | --- |
| `LLM_PROVIDER` | Optional. One of `xiaomi_openai`, `gemini`, `llama_cpp`, `auto` (default). When unset the provider is inferred from the keys below |
| `MIMO_API_KEY` | Xiaomi MiMo key (checked first). Keys starting with `tp-` select the MiMo provider |
| `MIMO_BASE_URL` | Optional, defaults to `https://token-plan-cn.xiaomimimo.com/v1` |
| `MIMO_MODEL` | Optional, defaults to `mimo-v2.5-pro` |
| `GEMINI_API_KEY` | Google Gemini key, used when no MiMo key is set |
| `LLAMA_BASE_URL` | llama.cpp endpoint, defaults to `http://127.0.0.1:8080/v1` |
| `LLAMA_MODEL` | Optional model name sent to llama-server, defaults to `local-model` |
| `LLAMA_API_KEY` | Optional, only if llama-server was started with `--api-key` |
| `LLAMA_MAX_TOKENS` | Optional, output budget for llama.cpp. Defaults to 4x the per-mode budget (min 900) because reasoning models burn tokens on `reasoning_content` before answering |
| `LLAMA_THINKING` | Optional, `0` (default) disables the model's reasoning mode via the chat template, `1` keeps it on, `none` sends no template kwargs at all |
| `LLM_TIMEOUT_SECONDS` | Optional request timeout, defaults to 30s (cloud) / 300s (llama.cpp) |

**bash / zsh — current shell only:**

```bash
# Xiaomi MiMo
export MIMO_API_KEY="paste-your-key-here"
export MIMO_BASE_URL="https://token-plan-sgp.xiaomimimo.com/v1"
export MIMO_MODEL="mimo-v2.5-pro"

# or Google Gemini
export GEMINI_API_KEY="paste-your-key-here"

# or a local llama.cpp server
export LLM_PROVIDER=llama_cpp
```

**bash / zsh — persist across sessions:**

```bash
echo 'export MIMO_API_KEY="paste-your-key-here"' >> ~/.bashrc   # bash
echo 'export MIMO_API_KEY="paste-your-key-here"' >> ~/.zshrc    # zsh
source ~/.bashrc      # or: source ~/.zshrc
```

**PowerShell (persists for your user):**

```powershell
setx MIMO_API_KEY "paste-your-key-here"
setx MIMO_BASE_URL "https://token-plan-sgp.xiaomimimo.com/v1"
setx MIMO_MODEL "mimo-v2.5-pro"
```

or for Gemini:

```powershell
setx GEMINI_API_KEY "paste-your-key-here"
```

or for a local llama.cpp model (see below):

```powershell
setx LLM_PROVIDER "llama_cpp"
```

**Current session only:**

```powershell
$env:MIMO_API_KEY = "paste-your-key-here"
```

Re-open any already-running terminal after `setx` so the new value is picked up.

### Local model with llama.cpp (no API key, no internet)

**A. Build llama.cpp** — pick one:

```bash
# macOS / Linux, from source
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build -DGGML_CUDA=OFF          # -DGGML_CUDA=ON with an NVIDIA GPU
cmake --build build --config Release -j  # binaries in build/bin/
cd ..
```

```bash
# macOS with Homebrew
brew install llama.cpp
```

```powershell
# Windows — grab a prebuilt release (no build tools needed)
# https://github.com/ggml-org/llama.cpp/releases  ->  llama-<version>-bin-win-cpu-x64.zip
```

**B. Download a GGUF model** (any chat model works), e.g.:

```bash
mkdir -p models
# example: any .gguf from https://huggingface.co/models?search=gguf
curl -L -o models/chat-model.gguf "https://huggingface.co/<org>/<model>/resolve/main/<file>.gguf"
```

**C. Start the OpenAI-compatible server:**

```bash
# from source build
./llama.cpp/build/bin/llama-server -m models/chat-model.gguf --host 127.0.0.1 --port 8080 -c 8192 -ngl 99

# Homebrew
llama-server -m models/chat-model.gguf --host 127.0.0.1 --port 8080 -c 8192
```

```powershell
# Windows
.\llama-server.exe -m C:\models\chat-model.gguf --host 127.0.0.1 --port 8080 -c 8192
```

`-ngl 99` offloads all layers to the GPU — drop it (and add `-t 8`) on CPU-only machines.
Leave this terminal running; stop the server with `Ctrl+C`.

**D. Verify it answers:**

```bash
curl http://127.0.0.1:8080/health                      # {"status":"ok"}
curl http://127.0.0.1:8080/v1/models                   # list of loaded models

curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"local-model",
       "messages":[{"role":"user","content":"say hi"}],
       "max_tokens":256,
       "chat_template_kwargs":{"enable_thinking":false}}'
```

You want a reply whose `message.content` is **not empty**. If `content` is `""` while `reasoning_content` is full, the model spent the whole budget thinking — raise `max_tokens` (that is exactly what `LLAMA_MAX_TOKENS` does for you in the simulator).

```powershell
# Windows (PowerShell instead of curl)
# NOTE: in PowerShell `curl` is an alias of Invoke-WebRequest — call curl.exe,
# and put the JSON in a file to avoid quoting problems:
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:8080/v1/models

'{"model":"local-model","messages":[{"role":"user","content":"say hi"}],"max_tokens":256,"chat_template_kwargs":{"enable_thinking":false}}' |
  Set-Content req.json -Encoding Ascii -NoNewline
curl.exe -s http://127.0.0.1:8080/v1/chat/completions -H "Content-Type: application/json" --data-binary "@req.json"
```

**E. Point the simulator at it** (either is enough):

```bash
# explicit
export LLM_PROVIDER=llama_cpp
# or just by endpoint — a loopback URL is auto-detected as llama.cpp
export LLAMA_BASE_URL="http://127.0.0.1:8080/v1"
```

```powershell
$env:LLM_PROVIDER = "llama_cpp"
```

Notes:

- `llama_cpp` is the generic **OpenAI-compatible** backend: it calls `/v1/chat/completions` with `max_tokens` and an optional `Authorization: Bearer` header, so llama-server, LM Studio, vLLM and Ollama's `/v1` endpoint all work. Set `LLM_PROVIDER=llama_cpp` explicitly for a non-loopback URL (loopback addresses are auto-detected).
- `LLAMA_MODEL` is not required — llama-server accepts any model name. Set it only if your server validates names.
- Small local models often ignore the "JSON only" instruction; the simulator retries on malformed replies and falls back to a neutral decision instead of crashing.
- Reasoning models (Qwen3 and friends) spend the output budget on `reasoning_content` first. The router already multiplies the budget and disables thinking by default — override with `LLAMA_MAX_TOKENS` / `LLAMA_THINKING` if needed.
- Slow hardware? Raise the timeout: `export LLM_TIMEOUT_SECONDS=600`.

## Run

### Windows (easiest)

Double-click **`start_ai_stock_simulator.bat`**. It launches the simulation, launches the dashboard, and opens `http://127.0.0.1:5000`.
Stop everything with **`stop_ai_stock_simulator.bat`**.

### macOS / Linux — two terminals

```bash
# terminal 1 — simulation engine
cd /path/to/ai_stock_simulator
source .venv/bin/activate
export LLM_PROVIDER=llama_cpp          # or MIMO_API_KEY / GEMINI_API_KEY
python simulation_server.py
```

```bash
# terminal 2 — web dashboard
cd /path/to/ai_stock_simulator
source .venv/bin/activate
python web_monitor.py
```

Then open <http://127.0.0.1:5000>.

### Run in the background (bash)

```bash
source .venv/bin/activate

nohup python simulation_server.py > simulation.log 2>&1 &
echo "simulation pid: $!"

nohup python web_monitor.py > web.log 2>&1 &
echo "dashboard pid:   $!"

# watch them
tail -f simulation.log web.log

# stop
pkill -f simulation_server.py
pkill -f web_monitor.py
```

The first run creates `server_society.db` and the runtime logs. Because `agents/` is empty, the market starts with **no agents** — create a few (below) and restart the simulation, or create them from the dashboard while it runs.

### Verify it is running

```bash
python db_health_check.py                 # database sanity report
curl -s http://127.0.0.1:5000/api/health  # {"status": ...} from the dashboard
tail -f simulation.log                    # watch the simulation rounds go by
```

```powershell
# Windows
python db_health_check.py
Invoke-RestMethod http://127.0.0.1:5000/api/health
Get-Content simulation.log -Wait
```

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ModuleNotFoundError: flask` | You are outside the venv — `source .venv/bin/activate`, then re-run |
| `No API key configured` warning | Set `MIMO_API_KEY` / `GEMINI_API_KEY`, or start llama.cpp and `export LLM_PROVIDER=llama_cpp` |
| Every decision says `System fallback` | Look at the `AgentBrain` log lines above it: invalid JSON (model too weak) or an empty reply (raise `LLAMA_MAX_TOKENS`) |
| llama.cpp refused the request | Check the server console; if it rejects unknown fields, `export LLAMA_THINKING=none` |
| `Connection refused` on port 8080 | `llama-server` is not running — start step C above, or check `curl http://127.0.0.1:8080/health` |
| Dashboard loads but shows nothing | The simulation must be running too — check `python db_health_check.py` |

---

## Creating agents

### Option A — from the dashboard

Open the **Create Agent** view and fill in display name, role, starting cash, salary, cost multiplier and a persona prompt. The agent is written to `agents/` and injected into the database immediately.

### Option B — by hand

Drop a JSON file into `agents/`:

```json
{
  "id": "Agent_MyTrader_001",
  "role": "RetailInvestor",
  "cash": 100000,
  "salary": 500,
  "cost_multiplier": 1.0,
  "traits": "Aggressive momentum trader. Loves hype, hates boredom, sells on any dip larger than 5%."
}
```

`setup_world()` picks up new files on the next simulation start.

### Option C — POST API

```bash
curl -X POST http://127.0.0.1:5000/api/create-agent \
  -H "Content-Type: application/json" \
  -d '{"display_name":"My Trader","role_name":"RetailInvestor","base_prompt":"Momentum chaser","starting_cash":100000,"salary":500}'
```

Other dashboard endpoints: `/api/health`, `/api/data`, `/api/post-bulletin`, `/api/agent/<id>/disable`, `/api/agent/<id>/delete`.

## Tests

These scripts hit the live LLM, so they consume API quota:

```bash
python test_forum.py        # forum only, no LLM calls
python test_single_agent.py # one agent: market + social decision
python test_reflection.py   # reflection / memory round
python test_social_posts.py # one social round across agents
python test_full_system.py  # full integration pass
python mvp_test.py          # minimal end-to-end demo
python db_health_check.py   # database sanity report
```

They read the same environment variables as the simulator (`MIMO_API_KEY` / `GEMINI_API_KEY` / `LLM_PROVIDER`).

---

## Runtime data (already in `.gitignore`)

Generated while running — **do not commit these**:

- `server_society.db` — full simulation state
- `simulation_status.json` — live status snapshot
- `*.log` (`simulation_*`, `web_*`) — process output; local logs can leak your **user name** and **IP address**
- `trade_log.md`, `forum_log.md`, `daily_audit_log.md` — generated histories (can be many MB)

Always review `git status` before `git add .`.

## Security notes — please read

1. **Never hardcode API keys.** This release reads them from environment variables only. If a key ever lands in a commit, **rotate it immediately** — GitHub history keeps secrets even after you delete the file.
2. **The dashboard now defaults to safe settings.** `web_monitor.py` binds to `127.0.0.1` with the Werkzeug debugger **off**, so nothing is reachable from your network out of the box. Changing that requires opting in via environment variables:

   | Variable | Default | Meaning |
   | --- | --- | --- |
   | `WEB_MONITOR_HOST` | `127.0.0.1` | Bind address. Only set this to `0.0.0.0` behind a reverse proxy you control. |
   | `WEB_MONITOR_DEBUG` | `0` | `1` enables Flask debug mode (interactive console). Never enable it on a shared network. |
   | `DASHBOARD_TOKEN` | *(unset)* | When set, every state-changing request must send it in the `X-Dashboard-Token` header; the dashboard page reads it from a meta tag automatically. |

   Independently of those settings, every `POST` endpoint rejects requests that lack an `X-Requested-With: XMLHttpRequest` header or whose `Origin` / `Referer` is not this host, so a plain cross-site HTML form can never create, disable, or delete an agent. **Never port-forward the dashboard to the public internet** — put it behind a real auth proxy instead.
3. **Runtime files can contain personal data** (paths, IPs, generated text). The `.gitignore` covers them; double-check before publishing.
4. This project makes real LLM API calls — treat your key as a billable secret and restrict it (quota, allowed IPs, separate key for experiments).

## License

Add a license of your choice before publishing.
