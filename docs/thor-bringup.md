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
| **C** | Nano 30B，NGC 的 Thor vLLM（最穩的退路；NemoClaw on Jetson 官方教學用的就是這顆） | `VLLM_PROFILE=generic IMAGE=nvcr.io/nvidia/vllm:26.01-py3 MODEL_ID=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4 REASONING_PARSER=nano_v3 scripts/serve-vllm.sh` |
| **D** | 同 C，改用 upstream 的 vLLM nightly 映像 | 把 C 的 `IMAGE` 拿掉，預設就是 `vllm/vllm-openai:nightly-77154ca` |

- 拿不準要帶什麼參數時，先加 `DRY_RUN=1`，它只會印出完整的 `docker run` 指令，不會真的啟動。
- 開機後的參數存在 `logs/vllm-edge-args.txt`；失敗原因看 `docker logs vllm-edge | tail -50`。
- 常見的失敗與解法見第 9 節：b12x 或 FlashInfer 不支援 sm_110、記憶體不夠、parser 名稱不對。

**通過條件**：
```bash
scripts/serve-vllm.sh status                                      # health: ok, models: ['edge-agent']
VLLM_URL=http://127.0.0.1:8000 .venv/bin/python scripts/smoke.py  # SMOKE OK（pong + 解析成功的 tool call）
```
把用了哪個做法、載入時間、`free -g` 的峰值記在第 8 節。**選 C 或 D 的話，簡報要把 Tier-2 改寫成 Nano 30B，計分卡改引用 Nano 的成績。**

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
- **sandbox 的網段**：`docker network inspect openshell-docker` 確認是不是 `172.24.0.0/16`；不是的話，設 `SANDBOX_NET`。
- **MIG**：`/etc/nv_tegra_release` 低於 7.2，就沒有 MIG。簡報的說法要改成「以 Tier 路由加 nvpmodel 達成資源治理」。

**通過條件**：preflight 除了 wan（若沒有 sudo）以外全部 PASS；台上筆電經會場網路打開 `http://<Thor IP>:8097/` 能看到畫面持續更新。

## 8. 紀錄表（寫完拍照傳到群組）

| 項目 | 值 |
|---|---|
| 機型／記憶體 | T5000 128 GB ／ T4000 64 GB |
| `/etc/nv_tegra_release` | |
| 推論做法（3 的 A/B/C/D）、映像 tag、模型 | |
| 從啟動到 ready 的秒數；`free -g` 的峰值 | |
| smoke 結果；5 張卡的結果 | |
| nvpmodel 模式表（id、名稱、瓦數）；切換一次要幾秒 | |
| tegrastats 的電源軌名稱；閒置與推論時的瓦數 | |
| sandbox 的網段；有沒有免密碼 sudo | |

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

相關文件：`docs/demo.md`（舞台流程與 §6.1 的啟動順序）、`docs/integration-vllm.md`（為什麼用 vLLM）、`docs/nemo-integration.md`（Guardrails 與 profiler）、`docs/hackathon-runbook.md`（Jetson 對照表，TRT 時期寫的）。
