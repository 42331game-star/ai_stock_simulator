# AI Stock Simulator（AI 股票模擬器）

一個由 **LLM 驅動的投資人社會**所組成的市場模擬玩具：每個 Agent 有自己的個性與記憶，會在虛擬市場裡交易四家虛構的 AI 公司、在虛擬論壇上發文互相嘴砲，並在每天收盤後反思自己的決策 —— 全部都可以在一個即時網頁儀表板上觀看。

> ⚠️ 這是玩具／研究用的實驗專案，不是交易工具。
> 所有公司、股票代號、價格與「新聞」都是虛構的，內容不構成任何投資建議。

---

## 它在做什麼

- **虛擬時鐘** — 一個模擬交易日 = 27 個開盤 tick + 2 個收盤 tick（`main_loop.VirtualClock`），每個 tick 預設睡 1 秒，所以「一天」很快就跑完。
- **開盤回合（Market Round）** — 每個啟用中的 Agent 會收到一組 prompt（人格特質、記憶、持倉、市場情境、基本面），回傳 JSON 交易清單，由 `market_engine.py` 撮合訂單並推動價格。
- **收盤回合（Social Round）** — Agent 讀取虛擬論壇動態，決定發文、回文或沉默，言論會影響市場情緒。
- **每日結算** — 發薪水扣生活費、中央政策事件、**反思回合**（信念／情景／人際／基本面記憶）、基本面事件（財報利多利空、財測、股利）、對股價過高的標的自動拆股、資料庫健康檢查與每日稽核日誌。
- **資料保存** — 全部落在 SQLite 資料庫（`server_society.db`）與幾個 Markdown 日誌。
- **網頁儀表板** — Flask 應用：即時狀態、日線／盤中 K 棒、財富排行榜、Agent 詳情、成交與論壇動態、政策公告，並可直接建立 Agent 或發布公告。

## 檔案結構

| 路徑 | 用途 |
| --- | --- |
| `simulation_server.py` | 主模擬迴圈、世界初始化、各回合與結算 |
| `market_engine.py` | 訂單撮合與價格動態 |
| `forum_system.py` | 虛擬論壇（發文、回文、熱門文章） |
| `llm_router.py` | LLM 抽象層：prompt 模式、JSON 驗證、重試與 fallback |
| `database.py` | SQLite 資料表與查詢 |
| `main_loop.py` | 虛擬時鐘／市場狀態 |
| `stock_split_service.py`、`stock_split.py` | 拆股規則與 CLI 工具 |
| `web_monitor.py` | Flask 儀表板（`http://127.0.0.1:5000`） |
| `templates/index.html` | 儀表板前端 |
| `db_health_check.py`、`monitor_market.py` | 小型診斷工具 |
| `agents/` | 一個 Agent 一個 JSON 檔（**發布版為空的 —— 自己建立陣容**） |
| `test_*.py`、`mvp_test.py` | 手動測試腳本 |
| `start_ai_stock_simulator.bat`、`stop_ai_stock_simulator.bat` | Windows 啟動／停止腳本 |

---

## 環境需求

- **Python 3.10 以上**（開發環境為 3.14）
- **git** 與一個 shell —— bash / zsh、PowerShell 或 cmd
- 一種 LLM：**小米 MiMo**（OpenAI 相容）、**Google Gemini**，或**本地 llama.cpp 模型**（不需任何金鑰）

兩個 `.bat` 腳本僅適用 Windows，下面其餘指令在 macOS、Linux、Windows 都能照打。

## 安裝

### 1. 取得程式碼

```bash
git clone https://github.com/42331game-star/ai_stock_simulator.git
cd ai_stock_simulator
```

### 2. 安裝 Python 3.10+

```bash
# macOS（Homebrew）
brew install python3

# Debian / Ubuntu
sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip git
```

```powershell
# Windows —— winget，或到 https://www.python.org/downloads/ 下載安裝檔
winget install Python.Python.3.12
```

確認版本：bash 用 `python3 --version`，Windows 用 `py -3 --version`。

### 3. 建立虛擬環境並安裝相依套件

```bash
# macOS / Linux（bash 或 zsh）
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
:: Windows cmd —— 若 PowerShell 執行原則擋住 activate 就用這個
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
```

確認套件都裝好了：

```bash
python -c "import flask, google.generativeai; print('dependencies OK')"
```

> 每開一個**新的**終端機都要重新 `source .venv/bin/activate`（Windows 為 `.venv\Scripts\activate.bat`），
> 才能使用下面的 `python ...` 指令。`start_ai_stock_simulator.bat` 自己會找 Python，不需要先 activate。

### 4. 設定 LLM —— 見下一節

## 設定 LLM（金鑰絕對不要寫死在程式碼裡）

模擬器**只會**從環境變數讀取設定：

| 環境變數 | 說明 |
| --- | --- |
| `LLM_PROVIDER` | 選填。`xiaomi_openai`、`gemini`、`llama_cpp`、`auto`（預設）之一。未設定時會依下面的金鑰自動判斷 |
| `MIMO_API_KEY` | 小米 MiMo 金鑰（優先檢查）。以 `tp-` 開頭的金鑰會自動選用 MiMo 供應商 |
| `MIMO_BASE_URL` | 選填，預設 `https://token-plan-cn.xiaomimimo.com/v1` |
| `MIMO_MODEL` | 選填，預設 `mimo-v2.5-pro` |
| `GEMINI_API_KEY` | Google Gemini 金鑰，未設定 MiMo 時使用 |
| `LLAMA_BASE_URL` | llama.cpp 端點，預設 `http://127.0.0.1:8080/v1` |
| `LLAMA_MODEL` | 選填，送給 llama-server 的模型名稱，預設 `local-model` |
| `LLAMA_API_KEY` | 選填，只有 llama-server 用 `--api-key` 啟動時才需要 |
| `LLAMA_MAX_TOKENS` | 選填，llama.cpp 的輸出 token 上限。預設為各模式需求的 4 倍（最少 900），因為推理模型會先把 token 花在 `reasoning_content` 上 |
| `LLAMA_THINKING` | 選填，`0`（預設）透過 chat template 關閉推理模式、`1` 保持開啟、`none` 完全不送 template 參數 |
| `LLM_TIMEOUT_SECONDS` | 選填，單次請求逾時秒數，預設雲端 30s、llama.cpp 300s |

**bash / zsh —— 只在目前 shell 生效：**

```bash
# 小米 MiMo
export MIMO_API_KEY="貼上你的金鑰"
export MIMO_BASE_URL="https://token-plan-sgp.xiaomimimo.com/v1"
export MIMO_MODEL="mimo-v2.5-pro"

# 或 Google Gemini
export GEMINI_API_KEY="貼上你的金鑰"

# 或本地 llama.cpp 伺服器
export LLM_PROVIDER=llama_cpp
```

**bash / zsh —— 永久生效：**

```bash
echo 'export MIMO_API_KEY="貼上你的金鑰"' >> ~/.bashrc   # bash
echo 'export MIMO_API_KEY="貼上你的金鑰"' >> ~/.zshrc    # zsh
source ~/.bashrc      # 或 source ~/.zshrc
```

**PowerShell（對你的使用者永久生效）：**

```powershell
setx MIMO_API_KEY "貼上你的金鑰"
setx MIMO_BASE_URL "https://token-plan-sgp.xiaomimimo.com/v1"
setx MIMO_MODEL "mimo-v2.5-pro"
```

或使用 Gemini：

```powershell
setx GEMINI_API_KEY "貼上你的金鑰"
```

或使用本地 llama.cpp 模型（詳見下一節）：

```powershell
setx LLM_PROVIDER "llama_cpp"
```

**只在目前终端機生效：**

```powershell
$env:MIMO_API_KEY = "貼上你的金鑰"
```

執行過 `setx` 之後，請重新開一個終端機再啟動程式。

### 用 llama.cpp 跑本地模型（免金鑰、免網路）

**A. 編譯 llama.cpp** —— 三選一：

```bash
# macOS / Linux，從原始碼編譯
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build -DGGML_CUDA=OFF          # 有 NVIDIA 顯卡就改 -DGGML_CUDA=ON
cmake --build build --config Release -j  # 產物在 build/bin/
cd ..
```

```bash
# macOS 用 Homebrew
brew install llama.cpp
```

```powershell
# Windows —— 直接下預編譯版，不需要編譯工具
# https://github.com/ggml-org/llama.cpp/releases  ->  llama-<version>-bin-win-cpu-x64.zip
```

**B. 下載 GGUF 模型**（任何聊天模型皆可），例如：

```bash
mkdir -p models
# 範例：從 https://huggingface.co/models?search=gguf 挑一個 .gguf
curl -L -o models/chat-model.gguf "https://huggingface.co/<org>/<model>/resolve/main/<file>.gguf"
```

**C. 啟動 OpenAI 相容伺服器：**

```bash
# 原始碼編譯版
./llama.cpp/build/bin/llama-server -m models/chat-model.gguf --host 127.0.0.1 --port 8080 -c 8192 -ngl 99

# Homebrew
llama-server -m models/chat-model.gguf --host 127.0.0.1 --port 8080 -c 8192
```

```powershell
# Windows
.\llama-server.exe -m C:\models\chat-model.gguf --host 127.0.0.1 --port 8080 -c 8192
```

`-ngl 99` 表示全部層丟到 GPU；純 CPU 機器請拿掉，並加上 `-t 8`。
這個終端機要保持開著，`Ctrl+C` 即可關閉伺服器。

**D. 確認它有回應：**

```bash
curl http://127.0.0.1:8080/health                      # {"status":"ok"}
curl http://127.0.0.1:8080/v1/models                   # 已載入的模型清單

curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"local-model",
       "messages":[{"role":"user","content":"say hi"}],
       "max_tokens":256,
       "chat_template_kwargs":{"enable_thinking":false}}'
```

要看 `message.content` **不是空的**才算成功。若 `content` 是 `""` 而 `reasoning_content` 塞滿，代表模型把預算全花在思考上 —— 把 `max_tokens` 調大（模擬器這邊已經由 `LLAMA_MAX_TOKENS` 自動處理）。

```powershell
# Windows（用 Invoke-RestMethod 取代 curl）
# 注意：PowerShell 的 curl 是 Invoke-WebRequest 的別名 —— 要下 curl.exe，
# 且建議把 JSON 寫進檔案，避免引號被吃掉：
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:8080/v1/models

'{"model":"local-model","messages":[{"role":"user","content":"say hi"}],"max_tokens":256,"chat_template_kwargs":{"enable_thinking":false}}' |
  Set-Content req.json -Encoding Ascii -NoNewline
curl.exe -s http://127.0.0.1:8080/v1/chat/completions -H "Content-Type: application/json" --data-binary "@req.json"
```

**E. 把模擬器指過去**（以下兩種擇一即可）：

```bash
# 明確指定供應商
export LLM_PROVIDER=llama_cpp
# 或者只給端點 —— 本機網址會被自動辨識成 llama.cpp
export LLAMA_BASE_URL="http://127.0.0.1:8080/v1"
```

```powershell
$env:LLM_PROVIDER = "llama_cpp"
```

注意事項：

- `llama_cpp` 就是通用的 **OpenAI 相容**後端：走 `/v1/chat/completions`、使用 `max_tokens`，並在有設定金鑰時帶上 `Authorization: Bearer`。因此 llama-server、LM Studio、vLLM、Ollama 的 `/v1` 端點都能用。若端點不是本機網址，請明確設定 `LLM_PROVIDER=llama_cpp`。
- `LLAMA_MODEL` 可以不設 —— llama-server 接受任何模型名稱；只有你的伺服器會驗證名稱時才需要設。
- 小型本地模型常不理会「只回 JSON」的指令；模擬器遇到格式錯誤會自動重試，仍然失敗就退回中性決策，不會當機。
- 推理模型（Qwen3 之類）會先把輸出預算花在 `reasoning_content` 上。路由器已經自動放大預算並預設關閉思考模式 —— 需要時可用 `LLAMA_MAX_TOKENS` / `LLAMA_THINKING` 覆寫。
- 硬體慢就把逾時拉長：`export LLM_TIMEOUT_SECONDS=600`。

## 執行

### Windows（最簡單）

直接點 **`start_ai_stock_simulator.bat`**，會依序啟動模擬引擎與儀表板，並自動開 `http://127.0.0.1:5000`。
要停止時執行 **`stop_ai_stock_simulator.bat`**。

### macOS / Linux —— 兩個終端機

```bash
# 終端機 1 — 模擬引擎
cd /path/to/ai_stock_simulator
source .venv/bin/activate
export LLM_PROVIDER=llama_cpp          # 或改設 MIMO_API_KEY / GEMINI_API_KEY
python simulation_server.py
```

```bash
# 終端機 2 — 網頁儀表板
cd /path/to/ai_stock_simulator
source .venv/bin/activate
python web_monitor.py
```

接著開啟 <http://127.0.0.1:5000>。

### 背景執行（bash）

```bash
source .venv/bin/activate

nohup python simulation_server.py > simulation.log 2>&1 &
echo "simulation pid: $!"

nohup python web_monitor.py > web.log 2>&1 &
echo "dashboard pid:   $!"

# 觀察輸出
tail -f simulation.log web.log

# 停止
pkill -f simulation_server.py
pkill -f web_monitor.py
```

第一次執行會建立 `server_society.db` 與各類日誌。因為 `agents/` 是空的，市場一開始**沒有任何 Agent** —— 請先建立幾個（見下）再重啟模擬，或直接在儀表板執行中新增。

### 確認是否正常運作

```bash
python db_health_check.py                 # 資料庫健檢報告
curl -s http://127.0.0.1:5000/api/health  # 儀表板回傳 {"status": ...}
tail -f simulation.log                    # 追蹤模擬回合輸出
```

```powershell
# Windows
python db_health_check.py
Invoke-RestMethod http://127.0.0.1:5000/api/health
Get-Content simulation.log -Wait
```

### 常見問題

| 症狀 | 解法 |
| --- | --- |
| `ModuleNotFoundError: flask` | 你不在虛擬環境裡 —— 先 `source .venv/bin/activate` 再執行 |
| 出現 `No API key configured` 警告 | 設定 `MIMO_API_KEY` / `GEMINI_API_KEY`，或啟動 llama.cpp 並 `export LLM_PROVIDER=llama_cpp` |
| 每個決策都是 `System fallback` | 看它上方的 `AgentBrain` 日誌：模型回了非 JSON（模型太弱）或空回覆（調高 `LLAMA_MAX_TOKENS`） |
| llama.cpp 拒絕請求 | 看伺服器主控台；若不接受未知欄位，`export LLAMA_THINKING=none` |
| Port 8080 `Connection refused` | `llama-server` 沒開 —— 回到上面步驟 C，或 `curl http://127.0.0.1:8080/health` 檢查 |
| 儀表板開了但沒資料 | 模擬引擎也要在跑 —— 用 `python db_health_check.py` 確認 |

---

## 建立 Agent

### 方式 A — 網頁儀表板

打開 **Create Agent** 頁面，填入名稱、角色、起始資金、薪水、成本倍數與人格 prompt。系統會寫入 `agents/` 並立刻注入資料庫。

### 方式 B — 手動建立

在 `agents/` 放一個 JSON 檔：

```json
{
  "id": "Agent_MyTrader_001",
  "role": "RetailInvestor",
  "cash": 100000,
  "salary": 500,
  "cost_multiplier": 1.0,
  "traits": "追高型動能交易者。熱愛題材、厭惡無聊，跌超過 5% 就殺出。"
}
```

下次啟動模擬時 `setup_world()` 會自動載入。

### 方式 C — HTTP API

```bash
curl -X POST http://127.0.0.1:5000/api/create-agent \
  -H "Content-Type: application/json" \
  -d '{"display_name":"我的交易員","role_name":"RetailInvestor","base_prompt":"追高不手軟","starting_cash":100000,"salary":500}'
```

其他端點：`/api/health`、`/api/data`、`/api/post-bulletin`、`/api/agent/<id>/disable`、`/api/agent/<id>/delete`。

## 測試

這些腳本會實際呼叫 LLM，會消耗你的 API 額度：

```bash
python test_forum.py        # 只測論壇，不呼叫 LLM
python test_single_agent.py # 單一 Agent 的決策測試
python test_reflection.py   # 反思／記憶回合
python test_social_posts.py # 全體 Agent 的社交回合
python test_full_system.py  # 完整整合測試
python mvp_test.py          # 最小端到端示範
python db_health_check.py   # 資料庫健康報告
```

它們同樣讀取與模擬器相同的環境變數（`MIMO_API_KEY` / `GEMINI_API_KEY` / `LLM_PROVIDER`）。

---

## 執行期產生的資料（已列入 `.gitignore`）

跑起來之後才產生 —— **不要提交**：

- `server_society.db` — 完整模擬狀態
- `simulation_status.json` — 即時狀態快照
- `*.log`（`simulation_*`、`web_*`）— 程序輸出；本機日誌可能包含你的**使用者名稱**與 **IP 位址**
- `trade_log.md`、`forum_log.md`、`daily_audit_log.md` — 產生的紀錄（可能好幾 MB）

執行 `git add .` 前請先看過 `git status`。

## 資安注意事項 — 請務必閱讀

1. **不要把 API Key 寫死在程式碼裡。** 本發布版只從環境變數讀取。若金鑰不小心進入過任何 commit，**請立刻到後台重設（rotate）** —— GitHub 即使刪掉檔案，歷史紀錄仍會保留祕密。
2. **儀表板預設已是安全設定。** `web_monitor.py` 只綁 `127.0.0.1`，且 Werkzeug 偵錯台**預設關閉**，開箱不會暴露在網段上。要改設定必須主動指定環境變數：

   | 環境變數 | 預設值 | 意義 |
   | --- | --- | --- |
   | `WEB_MONITOR_HOST` | `127.0.0.1` | 綁定位址。只有在你自己控管的反向代理背後才設成 `0.0.0.0`。 |
   | `WEB_MONITOR_DEBUG` | `0` | 設成 `1` 啟用 Flask debug 模式（互動式主控台）。絕對不要在共用網段開啟。 |
   | `DASHBOARD_TOKEN` | *（未設定）* | 一旦設定，所有會改動狀態的請求都要帶 `X-Dashboard-Token` 標頭；儀表板頁面會自動從 meta tag 讀取。 |

   不論上述設定為何，所有 `POST` 端點都會擋掉缺少 `X-Requested-With: XMLHttpRequest` 標頭、或 `Origin` / `Referer` 不是本機的請求 —— 一般跨站 HTML 表單無法建立、停用或刪除 Agent。**千萬不要對公网開 port**，請放在有驗證的反向代理後面。
3. **執行期檔案可能含個資**（路徑、IP、生成文字）。`.gitignore` 已涵蓋，發佈前再確認一次。
4. 這個專案會真的打 LLM API —— 請把金鑰當成會燒錢的祕密來管理（設定額度、限制來源，或實驗用另一把金鑰）。

## 授權條款

採用 **MIT** 授權，詳見 [`LICENSE`](LICENSE)。
