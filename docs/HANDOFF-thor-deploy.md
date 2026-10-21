# 交接：把 BlackOps 部署到比賽用的 Jetson AGX Thor（給接手的 Claude）

寫於 2026-10-21 中午，作者是 Spark 上的 Claude session（使用者 penny11124，GitHub `penny11124`）。
讀者：在使用者筆電上執行、能同時 ssh 到 Spark 和 Thor 的 Claude。
**回覆使用者一律用繁體中文。** 先讀完本文件，再讀 `docs/thor-bringup.md`（逐步流程）和 `docs/project-status-2026-10-20.md`（整體進度）。

## 0. 你要做的事

**讓目前 `main` 上的整套系統在 Thor 上跑起來，跑到能上台 demo 的程度**：照 `docs/thor-bringup.md` 的 §2 到 §7 做，每一步都要達成該步的「通過條件」才往下走。

| 時間 | 事項 |
|---|---|
| **10/22 16:00** | 比賽截止 |
| **10/22 11:00** | 硬性檢查點：第二幕（upstream-500 加人工審批加 Tier）在 Thor 上**連續成功 3 次**，才保留 Tier 和 nvpmodel；否則砍掉，只保留人工審批 |
| **今晚約 20:00** | 組員從新竹帶隨身碟出發去台北（離線包，內容見 §4） |

## 1. 機器

| | Spark（開發機，新竹） | **Thor（比賽機，台北會場）** |
|---|---|---|
| ssh | `hsnl@192.168.2.60` | `nv@192.168.1.184` |
| repo | `~/edge-agent`（branch `main`） | **`~/BlackOps`**；另建 symlink `~/edge-agent`，見 §3 |
| 硬體 | GB10，sm_121，119 GiB，**沒有 swap** | **T5000，122 GB，sm_110（compute 11.0），R38.4 = JetPack 7.1，沒有 MIG**，Docker 有 `nvidia` runtime，磁碟 771 GB 可用 |
| 推論服務 | `vllm-edge`：Bolt 74B-A9B，以 `edge-agent` 開在 :8000；`vllm-tier1`：Nano-4B-FP8 開在 :8010（generic profile，閒置中） | 還沒啟動 |
| 誰在用 | **多人共用**（帳號 `hsnl`），組員會在上面跑實驗（今天有 `bolt-hard` A/B、`~/edge-agent-hardening` worktree） | 只有我們這組 |

**Spark 由另一個 Claude session 負責**，也就是寫這份文件的 session。它負責 Tier 測試、在 Spark 上完整排練、回放素材、今晚的離線包更新和夜跑。**你只負責 Thor**。除非使用者明確要求，不要在 Spark 上啟動或停止服務、不要動 victim 容器、不要跑 drill。Spark 上的東西都是共用的，而且常駐服務會從工作目錄直接載入程式碼。

## 2. 規則：使用者的明確要求

- **Git 身分**：每一次 commit 都要用 `git -c user.name=penny11124 -c user.email=92087067+penny11124@users.noreply.github.com commit ...`。**不要改**任何機器上的共用 `~/.gitconfig`。
- **push**：使用者自己的 classic PAT，透過 `git -c credential.helper= push https://penny11124@github.com/Vanisherzd/BlackOps.git <branch>`。Spark 上 `gh` 登入的是別人的帳號（s102345），不要用它，也不要執行 `gh auth login` 或 `gh auth switch`。
- **不要在 Spark 的共用目錄切 branch**。需要改程式時，開 `git worktree`、開新 branch、commit、push，再開 PR 合進 `main`，PR description 用繁體中文。組員的分支（`kb-retrieval-v2`、`feat/blackbox-bolt-vllm`、`fix/ops-tool-safety-*`）都不要動。
- **不要改 `kb/` 底下的檔案**（使用者最早的要求）。
- **`main` 上的 #4、#5 是組員 Chaung, Chieh-Lin 合併的**，修了 ops 工具的安全規則和 rollback。要改 `scripts/agent.py`、`ops_api.py` 之前，先讀過這兩個 PR 的改動，避免衝突。
- **不可逆或對外的動作要先問使用者**：刪檔、`docker system prune`、刪 HF cache 或映像、`wan.sh down`、force push 都算。

## 3. Thor 目前的狀態（截至 12:00 左右）

已完成：
- `docs/thor-bringup.md` §1 的開機檢查，結果如 §1 的表。
- **真正的免密碼 sudo**：`/etc/sudoers.d/blackout-ops` 只放行 `iptables`、`ip6tables`、`nvpmodel`，`sudo -k` 之後測試仍然 OK。
- nvpmodel 模式表：**0 = MAXN、1 = 120W（開機預設）、2 = 90W、3 = 70W**。所以 `NVP_T1=3`（巡檢）、`NVP_T2=0`（推論）。目前是 MAXN。
- tegrastats 的電源軌：`VDD_GPU`、`VDD_CPU_SOC_MSS`、`VIN_SYS_5V0`、**`VIN`**。`VIN` 是模組總輸入，閒置約 22 W。
- repo 已 clone 到 `~/BlackOps`，`main` 是 `dc5f798`。

**還沒做**（`docs/thor-bringup.md` 第 2 段，使用者還沒回報結果）：
```bash
ln -s ~/BlackOps ~/edge-agent        # 必做：nemoclaw-drill.sh、drill-all.sh、run-archive.sh 都寫死 cd ~/edge-agent
cd ~/edge-agent && mkdir -p logs
nohup docker pull nvcr.io/nvidia/vllm:26.01-py3 > ~/dl-ngc.log 2>&1 &          # NGC 的 Thor vLLM 映像，壓縮後約 6 GB
pip install -q -U "huggingface_hub[cli]"; nohup hf download nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8 > ~/dl-4b.log 2>&1 &
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync      # 失敗就改：uv venv .venv --python 3.12 && VIRTUAL_ENV=$PWD/.venv uv pip install openai tenacity numpy
for m in 3 0; do t0=$(date +%s.%N); sudo -n nvpmodel -m $m; echo "-> $m: $(echo "$(date +%s.%N)-$t0"|bc) s"; done   # 量切換延遲
```

**`power.py` 的修正**（優先抓 `VIN`，不是 `VIN_SYS_5V0`）在 `fix/thor-power-rail` 分支（`9199e7f`，跟這份文件同一個分支），**可能還沒合進 `main`**。用 `git log origin/main --oneline | grep -i power` 確認；還沒合併的話，啟動 `power.py` 時加上 `POWER_TOTAL_RAIL=VIN`。

## 4. 接下來的順序（細節都在 `docs/thor-bringup.md`）

1. **推論（§3）**：先用已經下載到 Thor 的 4B 驗證整條路是通的：
   ```bash
   VLLM_PROFILE=generic IMAGE=nvcr.io/nvidia/vllm:26.01-py3 MODEL_ID=nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8 REASONING_PARSER=nano_v3 scripts/serve-vllm.sh
   VLLM_URL=http://127.0.0.1:8000 .venv/bin/python scripts/smoke.py
   ```
   - 4B 在 Spark 上用 generic profile 實際啟動過（Spark 用的是 vllm-openai nightly 映像），約 3 分鐘 ready。
   - **正式 demo 要用 Bolt 或 Nano 30B**，照 A→D 依序試：A 是 `scripts/serve-vllm.sh`（Bolt 原樣）；B 是 Bolt 加 NGC 映像；C 是 Nano 30B 加 NGC 映像；D 是 Nano 加 upstream 映像。
   - 一次只能開一台推論服務。要換的話，先 `scripts/serve-vllm.sh stop`。
2. **常駐服務（§4）**：shim（`bash scripts/shim-env.sh "SHIM_THINK=1"`）、ops_api（用 pid 檔）、`faults.py setup`、`rag.py build`。
3. **NemoClaw（§5）**：`NEMOCLAW_SANDBOX_GPU=0 COMPATIBLE_API_KEY=none scripts/bootstrap-spark.sh nemoclaw`，需要網路。會場網路可用。
4. **回歸（§6）**：5 張舞台卡用 NemoClaw 路徑跑。
5. **demo 外殼（§7）**：Guardrails sidecar（`.nemo-venv` 要在 Thor 上重建）、`power.py`、`watchdog.py`、`dashboard.py`（:8097）、`wan.sh`、Tier（需要 Tier-1：`T1_MODEL=nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8 REASONING_PARSER=nano_v3 scripts/serve-tier1.sh`，shim 和 ops_api 都要帶 `TIER_MODE=adaptive UPSTREAM_T1=http://127.0.0.1:8010`；nvpmodel 連動加 `TIER_NVPMODEL=1 NVP_T1=3 NVP_T2=0`）。

**以下 6 項從來沒有實際跑過**，在 Thor 上每一項都要至少跑一次，並回報結果：
- watchdog 自動叫醒 NemoClaw agent。
- dashboard 的按鈕和核准卡（`DASH_ACTIONS=1 APPROVAL=manual`）。
- 人工審批走 NemoClaw 路徑。
- `wan.sh down --yes` 和 `wan.sh up`。Thor 是我們自己的機器，可以跑，但要先確認不會切斷你自己連 Thor 的 ssh：它只擋非私有網段，`192.168.x` 不受影響。
- Tier 在兩台推論服務之間切換，加上 nvpmodel 連動。
- `run-archive.sh` 搭配 `power.py`，量 Thor 的功耗。

**離線包**（今晚約 20:00 從新竹出發）：隨身碟裡的 `/media/hsnl/TRANSCEND/thor-bundle-20261021/`，在 Thor 上掛載後路徑會不一樣。內容：
- 映像：`blackbox-vllm-bolt.tar`（24 GB，Bolt 的 b12x 映像，registry 上沒有）、`ngc-vllm.tar`、`vllm-openai-nightly.tar`、`nginx.tar`。
- 模型：`hf/bolt.tar`、`hf/nano.tar`、`hf/bge.tar`。
- `edge-agent.bundle`（git）、`wheels/`（NeMo，py3.12）。

載入方式見 `thor-bringup.md` §2。**Bolt（退路 A）只能靠這個隨身碟**；其他東西在會場也可以直接下載。

## 5. 已知的坑（都踩過）

- **`pgrep -f` 和 `pkill -f` 會比對到自己的指令列**：等待迴圈會永遠不結束，或把自己的 shell 殺掉。用 pid 檔，或寫成 `[x]` 這種形式避開自己。
- **常駐服務（ops_api、shim）啟動時就把程式載入記憶體**：改了程式要重啟服務才會生效。
- **背景程式一律用 `setsid nohup ... < /dev/null &`**，否則 ssh 斷線，程式就跟著被結束。
- **drill 被 timeout 中止時，sandbox 裡的 agent 還會繼續跑**：要執行 `nemoclaw edge-agent gateway restart`。每場 drill 之間要等 ops API 安靜 60 秒（`drill-all.sh` 會自動等）。
- **兩台大模型推論服務不要同時開**：Spark 10/17 曾經整台卡死，要實體重開機。Thor 有 122 GB，Bolt（0.45）加 Tier-1（0.12）在 Spark 上可以同時開，但要盯著 `free -g`。
- **Spark 上有一個 `rtk` shell 包裝**，會壓縮 `docker logs`、`git log` 這類指令的輸出：要計數或 grep 時，用 `command git ...` 或 `rtk proxy docker logs ...`。Thor 上沒有這個問題。
- **Guardrails 只檢查被監控系統自己的輸出**；agent.py 自己附加的提示不會被檢查（`scripts/guard.py` 的 `split_ours`）。換了模型要重跑 `scripts/test-guardrails.py`，因為 rail 是用本機模型判斷的。
- 模型 repo 裡附的 `<name>_reasoning_parser.py`（Nano：`nano_v3`，Bolt：`ultra_v3`）會被 generic profile 自動當成 plugin 載入。

## 6. 回報與文件

- **每完成一步，用一段話回報使用者**：做了什麼、通過條件有沒有達成、下一步是什麼。失敗時附上 `docker logs vllm-edge | tail -50` 這類的關鍵輸出。
- 填寫 `docs/thor-bringup.md` §8 的紀錄表：用哪條推論退路、ready 時間、記憶體峰值、5 張卡的結果、nvpmodel 切換延遲、閒置和推論時的瓦數。填完之後用 worktree 加 PR 的方式 commit 回去。
- 結論和數據寫進 `docs/project-status-2026-10-20.md` 和 `docs/demo.md`。**如果退到 Nano**，要在文件裡註明簡報和計分卡都要改成引用 Nano 的成績。

## 7. 參考文件

| 文件 | 用途 |
|---|---|
| `docs/thor-bringup.md` | **逐步流程**、四條推論退路、排錯表、紀錄表 |
| `docs/demo.md` | 舞台流程；§6.1 是 demo 服務的啟動順序；§3.1 是上台前檢查 |
| `docs/project-status-2026-10-20.md` | 整體進度、風險、時程 |
| `docs/integration-vllm.md` | 為什麼用 vLLM + Bolt，以及 Spark 上的驗證數據 |
| `docs/nemo-integration.md` | NeMo Guardrails 與 Agent Toolkit |
| `docs/HANDOFF-kb-retrieval-v2.md` | vanli 的交接文件：agent、guards、實驗方法、踩過的坑 |
| `README.md` | 架構、repo 結構、日常操作 |
