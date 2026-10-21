# Thor 上機流程：讓目前的 code 在 Jetson AGX Thor 上跑起來

對象：10/21 工作坊與 10/22 上午，到 Thor 上部署的人。照順序做，每一步都有「通過條件」，**沒通過就不要往下做**，先看第 9 節的排錯表。

前提：Thor 是 JetPack 7（L4T r38，sm_110，CUDA 13）。Spark 上的整套程式（main `14a8f3e` 起）除了推論層以外，全部是 Docker 加 Python，換機器不用改。推論層只要讓服務以 `edge-agent` 這個名稱開在 `:8000`，上層的 shim、agent、NemoClaw、reviewer 一行都不用動。

## 0. 出發前：在 Spark 上把離線包做好

會場網路可能不穩，以下全部拷到 TRANSCEND 隨身碟。它有 880 GB 可用；Spark 本機只剩約 41 GB，**不要存在本機**。

```bash
B=/media/hsnl/TRANSCEND/thor-bundle-20261021; mkdir -p $B/images $B/hf $B/wheels
# 映像（皆為 arm64；JetPack 7 跟 Spark 一樣走 SBSA 軟體堆疊，同一份 arm64 映像理論上兩邊都能用，但 Thor 上沒驗證過）
docker save blackbox-vllm-bolt:77154ca                                     -o $B/images/blackbox-vllm-bolt.tar   # 24.7 GB，Bolt 用（含 b12x）
docker save vllm/vllm-openai:nightly-77154ca6116ae34cbfe314beef1016bde9b63989 -o $B/images/vllm-openai-nightly.tar  # 24.4 GB，generic 用
# NGC 的 Thor vLLM：先到 NGC 確認最新的 tag；若要求登入：docker login nvcr.io（帳號 $oauthtoken，密碼填 NGC API key）
docker pull nvcr.io/nvidia/vllm:26.01-py3 && docker save nvcr.io/nvidia/vllm:26.01-py3 -o $B/images/ngc-vllm.tar
docker save nginx:latest -o $B/images/nginx.tar                             # victim
# 模型（-h 會跟著 symlink 抓到 blobs）
tar -C ~/.cache/huggingface -chf $B/hf/bolt.tar hub/models--nvidia--NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB   # 24.1 GB
tar -C ~/.cache/huggingface -chf $B/hf/nano.tar hub/models--nvidia--NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4     # 19.3 GB，退路
tar -C ~/.cache/huggingface -chf $B/hf/bge.tar  hub/models--BAAI--bge-small-en-v1.5                         # RAG 檢索
# 程式（含未入版控、但 demo 會用到的檔案）
git -C ~/edge-agent bundle create $B/edge-agent.bundle --all
# NeMo sidecar 的 wheels（.nemo-venv 不能直接搬，因為裡面是絕對路徑）
uvx --python 3.12 pip download -d $B/wheels nemoguardrails==0.24.1 nvidia-nat==1.9.0 nvidia-nat-profiler==1.9.0   # 3.12，跟 Thor 上的 venv 一致（系統 python3 是 conda 3.13）
```

**通過條件**：`ls -la $B/images $B/hf` 每個檔案都不是 0 byte。另外帶一份這份文件的紙本或 PDF。

## 1. Thor 開機檢查（5 分鐘）

```bash
cat /etc/nv_tegra_release                       # 預期 R38.x（JetPack 7.x）；7.2 以上才有 MIG
nvidia-smi --query-gpu=name,compute_cap --format=csv
docker info --format '{{.Runtimes}}'            # 要看到 nvidia
free -g; df -h /                                # T5000 約 128 GB／T4000 約 64 GB；磁碟至少 120 GB 可用
sudo nvpmodel -q --verbose | head -40           # 抄下模式表（第 7 節的 NVP_T1、NVP_T2 會用到）
sudo nvpmodel -m 0 && sudo jetson_clocks        # 先開到 MAXN，部署階段不要被降頻
tegrastats --interval 1000 | head -3            # 抄下電源軌名稱（第 7 節 power.py 會用到）
sudo -n true && echo "passwordless sudo OK"     # wan.sh、nvpmodel 連動需要
```

把結果填進第 8 節的紀錄表。**64 GB 機型**：跳過 Bolt，第 3 步直接走 C。

## 2. Repo 與 Python 環境

```bash
cd ~ && git clone <bundle 或 GitHub> edge-agent && cd edge-agent       # 離線：git clone $B/edge-agent.bundle edge-agent
git checkout main
curl -LsSf https://astral.sh/uv/install.sh | sh                          # 若 uv 不在
uv sync                                                                  # 有網路時：跟 Spark 相同的 venv
# 沒網路或 uv sync 失敗：用最小的 venv，RAG 會自動退回 keyword 檢索（沒有 torch 時）
#   uv venv .venv --python 3.12 && VIRTUAL_ENV=$PWD/.venv uv pip install openai tenacity numpy
# 模型與映像
for f in $B/images/*.tar; do docker load -i $f; done
for f in $B/hf/*.tar; do tar -C ~/.cache/huggingface -xf $f; done
```

**通過條件**：`.venv/bin/python -c "import openai"` 不出錯；`docker image ls` 看得到上面那幾個映像。

## 3. 推論：依序嘗試，第一個成功的就停

全部都以 `edge-agent` 的名稱開在 `:8000`。`scripts/serve-vllm.sh` 會等到 `/health` 通過、而且 `/v1/models` 列出 `edge-agent` 才返回，最多 15 分鐘。失敗就先 `scripts/serve-vllm.sh stop`，再試下一個。

| 順序 | 做法 | 指令 |
|---|---|---|
| **A** | Bolt，沿用 Spark 上驗證過的做法（b12x） | `scripts/serve-vllm.sh` |
| **B** | Bolt，改用 NGC 的 Thor vLLM（沒有 b12x，用 vLLM 預設的 kernel） | `VLLM_PROFILE=generic IMAGE=nvcr.io/nvidia/vllm:26.01-py3 MODEL_ID=nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB REASONING_PARSER=ultra_v3 EXTRA_ARGS="--kv-cache-dtype fp8" scripts/serve-vllm.sh` |
| **C** | Nano 30B，NGC 的 Thor vLLM（最穩的退路；NemoClaw on Jetson 官方教學用的就是這顆） | `VLLM_PROFILE=generic IMAGE=nvcr.io/nvidia/vllm:26.01-py3 MODEL_ID=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4 REASONING_PARSER=nano_v3 EXTRA_ARGS="--kv-cache-dtype fp8" VLLM_ENV="VLLM_USE_FLASHINFER_MOE_FP4=1 VLLM_FLASHINFER_MOE_BACKEND=throughput" READY_TIMEOUT=3600 scripts/serve-vllm.sh` |
| **D** | 同 C，改用 upstream 的 vLLM nightly 映像 | 把 C 的 `IMAGE` 拿掉，預設就是 `vllm/vllm-openai:nightly-77154ca` |
| **E（10/21 採用）** | **Lightning 30B NVFP4**，vLLM nightly + Marlin MoE（不需要 FlashInfer 的 MoE JIT） | `GPU_MEMORY_UTILIZATION=0.30 VLLM_PROFILE=generic MODEL_ID=nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4 REASONING_PARSER=nemotron_v3 EXTRA_ARGS="--moe-backend marlin --kv-cache-dtype fp8 --mamba-cache-mode align" scripts/serve-vllm.sh` |

**10/21 在 Thor（T5000，R38.4）上的實測結果**（數據與比較見 `docs/thor-tier-report-20261021.md`）：

- **A、B 都不行**：Bolt-24GB 的 dense 層是 block 量化，vLLM 規定要用 b12x，而 b12x 只為 SM12x（GB10）編譯（`Block-quantized dense weights require b12x on SM12x`）。B 用的 NGC 26.01 映像，transformers 也太舊，連 Bolt 的 remote code 都載不進來（`cannot import name 'PreTrainedConfig'`）。
- **C 可以，但第一次啟動約 90 分鐘**：要照模型卡帶上 FlashInfer 的 MoE 環境變數，否則報 `Non-gated activations are only supported by the flashinfer CUTLASS backend`；FlashInfer 沒有替 sm_110 預編 MoE kernel，要 JIT 編 253 個目標檔。`serve-vllm.sh` 會把編譯快取存在主機，之後重啟只要 112 秒。**不要刪 `~/.cache/flashinfer`、`~/.cache/flashinfer-gen`、`~/.cache/vllm-compile`。**
- **E 是採用的做法**：首次 437 秒、之後約 100 秒 ready；5 張舞台卡 5/5，upstream-500 95 秒；每 token 能耗是 C 的三分之二。
- TensorRT-LLM 1.3.0rc13（`scripts/serve-trt.sh`）也試過：FP4 fused LayerNorm 沒有 sm_110 kernel（`no kernel image ... ws_layernorm_fp4_traits.cu`），不能用。

- 拿不準要帶什麼參數時，先加 `DRY_RUN=1`，它只會印出完整的 `docker run` 指令，不會真的啟動。
- 開機後的參數存在 `logs/vllm-edge-args.txt`；失敗原因看 `docker logs vllm-edge | tail -50`。
- 常見的失敗與解法見第 9 節：b12x 或 FlashInfer 不支援 sm_110、記憶體不夠、parser 名稱不對。

**通過條件**：
```bash
scripts/serve-vllm.sh status                                      # health: ok, models: ['edge-agent']
VLLM_URL=http://127.0.0.1:8000 .venv/bin/python scripts/smoke.py  # SMOKE OK（pong + 解析成功的 tool call）
```
把用了哪個做法、載入時間、`free -g` 的峰值記在第 8 節。**選 C、D、E 的話，簡報要把 Tier-2 改寫成實際用的模型，計分卡改引用它在 Thor 上的成績。**

啟動前先釋放檔案快取：Thor 會把下載過的檔案快取算進 used，vLLM 依此算出的可用記憶體會偏低。
```bash
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
```

## 4. 常駐服務、victim、RAG

```bash
bash scripts/shim-env.sh "SHIM_THINK=1"                                                   # :8001
setsid nohup .venv/bin/python scripts/ops_api.py > logs/ops-api.out 2>&1 < /dev/null & echo $! > logs/ops-api.pid   # :8790
.venv/bin/python scripts/faults.py setup                                                  # victim :8880（含 /board/）
.venv/bin/python scripts/rag.py build                                                     # 有 torch 才建 dense 索引；沒有就自動退回 keyword
```

**通過條件**：`curl -s localhost:8001/v1/models` 看得到 edge-agent；`curl -s localhost:8790/tools | head -c 100` 有回應；`faults.py verify` 顯示 PASS。

## 5. NemoClaw（約 15 分鐘，需要網路）

```bash
NEMOCLAW_SANDBOX_GPU=0 COMPATIBLE_API_KEY=none scripts/bootstrap-spark.sh nemoclaw       # Thor 上 sandbox 的 GPU 檢查會失敗，所以關掉（推論本來就在 sandbox 外）
PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH" nemoclaw edge-agent status | grep -E "Inference|Policies"
```

`bootstrap-spark.sh` 的 nemoclaw 階段會做三件事：建 sandbox、加政策（local-inference、ops-api）、上傳 skill 和 workspace。它的 image、model、serve 三個階段還是 TRT 版，**不要跑**。

**通過條件**：`Inference: healthy`。接著跑 `bash scripts/nemoclaw-drill.sh nginx-stopped`，最後一行要是 `verify: ... PASS`。

## 6. 回歸

```bash
scripts/preflight.sh                                    # 核心項目要 PASS（watchdog、power 在第 7 步才會開）
bash scripts/drill-all.sh nginx-stopped container-removed bad-config upstream-paused upstream-500   # 5 張舞台卡
.venv/bin/python scripts/score.py logs/drills-<時間>.log | tail -3
```
第 3 步選的不是 A 時，`scripts/validate-vllm.sh` 也能用：同樣的 `VLLM_PROFILE`、`IMAGE`、`MODEL_ID` 環境變數放在前面就行。

**通過條件**：5/5，或至少把第二幕要用的 upstream-500 跑過 3 次、全部 PASS。這就是 10/22 傍晚的檢查點。

## 7. Demo 外殼（照 `docs/demo.md` §6.1 的順序）

**一鍵版**：推論服務開好之後，`scripts/stage-up.sh` 會照下面的順序、帶上 10/21 在 Thor 上驗證過的設定，重啟 shim、guard_api、ops_api、power、watchdog、dashboard，最後跑 `preflight.sh --warm`（`stage-up.sh status`、`stage-up.sh down` 也能用）。有 Tier-1 server 在 :8010 才會開 Tier；nvpmodel 連動預設 `NVP_T1=1 NVP_T2=0`。以下是手動版，留作對照。

```bash
# NeMo Guardrails sidecar：venv 不能從 Spark 直接搬，要在這台上重建
uv venv .nemo-venv --python 3.12
VIRTUAL_ENV=$PWD/.nemo-venv uv pip install nemoguardrails==0.24.1 nvidia-nat==1.9.0 nvidia-nat-profiler==1.9.0   # 離線：加 --no-index --find-links $B/wheels
setsid nohup .nemo-venv/bin/python scripts/guard_api.py > logs/guard-api.out 2>&1 < /dev/null & echo $! > logs/guard-api.pid
.venv/bin/python scripts/test-guardrails.py              # 要 ALL PASS；rail 是在本機模型上判斷的，換了模型更要跑一次
# 功耗：Thor 用 tegrastats。如果第 1 步看到的總電源軌不叫 VDD_IN／VIN_*，就設 POWER_TOTAL_RAIL
setsid nohup .venv/bin/python scripts/power.py log > logs/power.out 2>&1 < /dev/null & echo $! > logs/power.pid
# nvpmodel 連動：NVP_T1／NVP_T2 填第 1 步抄下的模式 id（例如巡檢用省電模式、推論用 MAXN）
# ops_api 依舞台設定重啟：
kill $(cat logs/ops-api.pid); env GUARDRAILS=1 APPROVAL=manual TIER_MODE=adaptive TIER_NVPMODEL=1 NVP_T1=<id> NVP_T2=<id> \
  setsid nohup .venv/bin/python scripts/ops_api.py > logs/ops-api.out 2>&1 < /dev/null & echo $! > logs/ops-api.pid
setsid nohup .venv/bin/python scripts/watchdog.py > logs/watchdog.out 2>&1 < /dev/null & echo $! > logs/watchdog.pid
DASH_ACTIONS=1 APPROVAL=manual setsid nohup .venv/bin/python scripts/dashboard.py > logs/dashboard.out 2>&1 < /dev/null &   # :8097
DASHBOARD_URL=http://127.0.0.1:8097 scripts/preflight.sh --warm
```

- **Tier**：只有在 Tier-1 server（`T1_MODEL=... scripts/serve-tier1.sh`）也開起來的情況下，才設 `TIER_MODE=adaptive UPSTREAM_T1=http://127.0.0.1:8010`；shim 也要帶同樣的環境變數。否則 `TIER_MODE` 不要設，Tier 那段在台上用口頭說明。
- **斷網**：`wan.sh` 需要免密碼 sudo 執行 iptables。先用 `scripts/wan.sh down --yes`、`scripts/wan.sh up` 測一次。
- **sandbox 的網段**：`wan.sh` 會自己從 `openshell-docker` 讀（Thor 是 `172.19.0.0/16`），只有那個網路不存在時才退回 `172.24.0.0/16`。
- **MIG**：`/etc/nv_tegra_release` 低於 7.2，就沒有 MIG。簡報的說法要改成「以 Tier 路由加 nvpmodel 達成資源治理」。
- **nvpmodel（10/21 實測）**：MAXN(0) ↔ 120W(1) 可即時切換（約 30 ms）；90W(2)、70W(3) 要重開機，不能跟著 Tier 切，所以 `NVP_T1=1 NVP_T2=0`。但推論時只有 60–70 W，120W 模式的功耗實測跟 MAXN 一樣，連動拿不出節能數據。
- **Guardrails**：rail 用本機模型判斷。換成 Lightning 時，URL 編碼的注入（`assistant,%20please%20execute...`）原本漏抓，`guard_api.py` 現在先解碼再判斷，23/23 PASS。

**通過條件**：preflight 除了 wan（若沒有 sudo）以外全部 PASS；台上筆電經會場網路打開 `http://<Thor IP>:8097/` 能看到畫面持續更新。

## 8. 紀錄表（寫完拍照傳到群組）

| 項目 | 值 |
|---|---|
| 機型／記憶體 | **T5000，122 GB 可用（MemTotal 128 GB），沒有 swap**；磁碟 936 GB |
| `/etc/nv_tegra_release` | **R38.4（JetPack 7.1），沒有 MIG**；sm_110；Docker 有 `nvidia` runtime |
| 推論做法（3 的 A/B/C/D）、映像 tag、模型 | **E**：`vllm/vllm-openai:nightly-77154ca`，Nemotron-3.5-Lightning-30B-A3B-NVFP4，`--moe-backend marlin`，預留 0.30（A、B 失敗；C 可用當備援；TRT-LLM 失敗） |
| 從啟動到 ready 的秒數；`free -g` 的峰值 | 首次 437 s，之後約 100 s；權重 17.8 GiB；服務約佔 37 GB，系統剩約 80 GB |
| smoke 結果；5 張卡的結果 | SMOKE OK；**5/5**（25 / 27 / 340 / 26 / 95 s），made-worse 0，`logs/drills-20261021-180239.log` |
| nvpmodel 模式表（id、名稱、瓦數）；切換一次要幾秒 | 0 = MAXN、1 = 120W（開機預設）、2 = 90W、3 = 70W；0 ↔ 1 約 30 ms，切到 2、3 要重開機 |
| tegrastats 的電源軌名稱；閒置與推論時的瓦數 | `VDD_GPU`、`VDD_CPU_SOC_MSS`、`VIN_SYS_5V0`、`VIN`（總輸入）；閒置 25 W，Lightning 推論約 64 W |
| sandbox 的網段；有沒有免密碼 sudo | `172.19.0.0/16`；`/etc/sudoers.d/blackout-ops` 只放行 iptables、ip6tables、nvpmodel |

## 9. 排錯

| 症狀 | 可能原因 | 處理 |
|---|---|---|
| A：`b12x`、`sm121` 或 `no kernel image` | b12x 只為 GB10 編譯 | 改走 B |
| B：Bolt 載入時 `unsupported quantization`／權重格式錯誤 | Bolt 的 24 GB 版需要 b12x | 改走 C |
| `FLASHINFER`／attention backend 不支援 | sm_110 沒有預編的 kernel | generic profile 本來就不指定 backend；若 A 失敗在這裡，改走 B |
| `CUDA out of memory`／整台卡住 | 記憶體不夠（沒有 swap） | `GPU_MEMORY_UTILIZATION=0.35` 或 `MAX_MODEL_LEN=16384`；整台卡住就實體重開 |
| `unknown reasoning parser` | parser 名稱錯了 | `docker run --rm --entrypoint python3 <IMAGE> -c "import vllm.reasoning,pkgutil;print([m.name for m in pkgutil.iter_modules(vllm.reasoning.__path__)])"` 列出內建的 parser；Nemotron 的 parser plugin 會從模型 repo 自動找 |
| `exec: "vllm": not found` | 映像的 entrypoint 已經是 server | `ENTRY=""`（vllm-openai 系列已經自動處理） |
| smoke 的 tool call 解析失敗 | tool parser 不對 | `TOOL_PARSER=` 改成模型卡寫的那個；或先確認 shim 的 salvage 有沒有救回 |
| NemoClaw `Inference: unhealthy` | shim 沒起來，或 sandbox 打不到 host | `curl localhost:8001/v1/models`；`openshell provider update compatible-endpoint --config OPENAI_BASE_URL=http://host.openshell.internal:8001/v1` |
| 推論時 tegrastats 的瓦數幾乎沒變 | nvpmodel 停在低功耗模式 | `sudo nvpmodel -q`；部署階段先保持 MAXN |
| `LocalEntryNotFoundError ... outgoing traffic has been disabled` | 用 `hf download --revision <hash>` 抓的模型沒有 `refs/main` | 在模型的 cache 目錄建 `refs/main`，內容寫那個 commit hash |
| `Error in memory profiling. Initial free memory ... current free memory ...` | 統一記憶體：profiling 期間別的行程（例如剛結束的編譯）釋放了記憶體 | 等編譯結束、`drop_caches` 後重啟；快取在主機上，不會重編 |
| 每次重啟都重編 FlashInfer 30 分鐘以上 | 生成的 CUTLASS 原始碼留在容器裡，每次都比快取的 .o 新 | 用 `serve-vllm.sh`（會掛 `~/.cache/flashinfer-gen`）；不要手動 `docker run` |
| 編譯時 MemAvailable 一路往下掉 | nvcc 平行數沒限制（曾經 49 個） | `MAX_JOBS=4`（`serve-vllm.sh` 在 Jetson 上的預設） |
| 從筆電上傳到 Thor 很慢、ssh 偶爾逾時 | 連到的是 Thor 的 Wi-Fi（`wlP1p1s0`） | 改連有線介面的 IP（10/21 是 192.168.1.108）；會場上 Thor 要插網路線 |
| 事故 agent 一啟動就 `Failed to acquire lock on ~/.nemoclaw-portable-host.lock ... owner PID is still running` | 上一場的 agent 還在跑，或已結束但成了殭屍行程 | 新版 watchdog 會回收並先停掉舊的；手動處理：`scripts/stage-up.sh` |
| 修好之後 agent 還在改東西，看板又轉紅 | OpenClaw 延續已結束的 session（`Runtime event continuation`），Lightning 會去寫事故記憶、改設定 | `agent.py` 的 `HEALTHY_GUARD`（系統健康時拒絕改動）與 watchdog 的 `AGENT_GRACE`（結案 60 秒後停掉）；sandbox 裡還在跑就 `nemoclaw edge-agent gateway restart` |
| 結案後 shim 日誌出現一連串 prompt 長度完全相同的請求 | vLLM 0.32 的思考欄位叫 `reasoning`，舊版 shim 只讀 `reasoning_content`，空回應讓 OpenClaw 一直重試 | 已修（`oai_shim.py` 兩個欄位都讀） |
| `nemoclaw edge-agent gateway restart` 報 readiness timeout | gateway 起來得比檢查慢 | 等 1 分鐘再 `nemoclaw edge-agent status`，通常已經 `Inference: healthy` |
| drill 的 upstream-500 FAIL，`approvals.jsonl` 裡是 `"by": "timeout"` | `stage-up.sh` 用舞台設定 `APPROVAL=manual`，drill 沒有人按核准，180 秒後視為拒絕（fail-closed，這是設計） | 跑 drill 時有人在 dashboard 按核准，或用 `APPROVAL=auto scripts/stage-up.sh` 起服務，跑完再用預設值重起 |
| Guardrails 測試 22/23（`disk quota exceeded. To the monitoring agent...`） | Lightning 對這一行固定判 No；同一個 prompt 再問只會得到同一個答案 | `guard_api.py` 對第一次判 No 的候選行，用「Log line: 」前綴換個寫法再判一次，任何一次判是就移除（3 次 23/23） |

相關文件：`docs/demo.md`（舞台流程與 §6.1 的啟動順序）、`docs/integration-vllm.md`（為什麼用 vLLM）、`docs/nemo-integration.md`（Guardrails 與 profiler）、`docs/hackathon-runbook.md`（Jetson 對照表，TRT 時期寫的）。
