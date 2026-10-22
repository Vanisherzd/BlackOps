# Benchmark 全部結果（2026-10-22，決賽日）

每一列：怎麼量、在哪台、來源檔。Thor＝比賽機（Jetson AGX Thor，基本配方：Lightning NVFP4 on vLLM，無推測解碼，reranker sidecar，審核不思考）；
Spark＝開發機（DGX Spark GB10）。沒跑過的不寫（ITBench 未跑，見 `docs/itbench-alignment-20261022.md`）。

## 1. Thor — drill（注入故障 → sandbox agent 修 → 驗證；`scripts/regression.sh`，`score.py` 統一計分）

**彙總（今日 27 場、15 種故障）**：自主修復 **23/27（85%）**、越修越壞 **0**、謊報健康 **0**；每種故障至少修成一次。
非自主的 4 場：fd 上限（修法上線前，604 s）、webroot 清空（兜底，305 s）、listen-localhost（605 s 未解）、一場 upstream-500 在 gateway 重啟期間 4 s 即中止（無效場）。

| 故障 | 類別 | 場次 | 自主 | 秒數（各場） | 備註 |
|---|---|---|---|---|---|
| nginx-stopped | 容器 | 2 | 2/2 | 50、— | 全目錄首 5 案第二場 PASS，秒數在未結算 log |
| bad-config | 設定 | 2 | 2/2 | 107、— | |
| upstream-down | 容器 | 2 | 2/2 | 171、— | |
| bad-upstream-name | 設定 | 2 | 2/2 | 124、— | |
| container-removed | 容器 | 2 | 2/2 | 14、— | 最快 |
| upstream-sigstop | 容器 | 1 | 1/1 | 462 | 24 次呼叫 |
| front-hosts-poisoned | 網路 | 1 | 1/1 | 155 | |
| front-proxy-buffer-too-small | 設定 | 2 | 2/2 | 118、46 | 第二場 reranker 開 |
| upstream-listen-localhost | 網路 | 2 | 1/2 | 459、605✗ | 唯一未解案 |
| front-listen-ipv6-only | 網路 | 2 | 2/2 | 273、219 | |
| front-rlimit-nofile | 資源 | 2 | 1/2 | 604✗、**64** | runbook＋log 指引＋修檔工具後修好（3 次呼叫） |
| upstream-webroot-wiped | 資料 | 2 | 1/2 | 305（兜底）、414 | |
| upstream-500 | 服務 | 3 | 2/3 | 103（舊規則：重建）、4✗（無效）、**141（修檔，不換容器）** | |
| upstream-wrong-content | 資料 | 1 | 1/1 | **86（修檔）** | 內容感知健康抓到一次錯誤修法 |
| upstream-index-deleted | 資料 | 1 | 1/1 | **158（修檔）** | |

來源：`logs/regression-20261022-{102225,122354,123415,115557,115009,131812}`（Thor）。

## 2. Thor — 舞台巡檢全路徑（巡檢偵測 → 開事故 → 派工 → 修復 → 結案；`logs/incidents.jsonl`）

| 故障 | 結案秒數（各場） |
|---|---|
| compound-bad-deploy-cascade（壞部署連環，三層） | 85、52、33、51 |
| upstream-500 | 32、42、37、168（該場與 serving benchmark 同時跑） |
| log-injection | 49、33、37 |
| compound-triple（三因） | 88 |
| compound-detached-paused | 65 |
| 偵測 → 開事故 | ≈4 s（注入 13:30:14 → 開單 13:30:18） |

未結案：upstream-wrong-content 在 13:49 那場 agent 自己寫錯頁面、重複改寫，600 s 由迴圈 reset（修法：runbook 改為先還原映像原檔，之後該案 86 s）。

## 3. Thor — 推論

**vLLM 官方 serving benchmark**（`vllm bench serve`，random 2048 入／256 出，24 題，`--ignore-eos`；量測期間 demo 迴圈的 agent 同時在修一場事故）

| 併發 | 輸出 tok/s | 請求/s | TTFT 平均／中位／P99 | TPOT 平均／中位／P99 | ITL 平均／中位 | 耗時 |
|---|---|---|---|---|---|---|
| 1 | **49.6** | 0.19 | 0.56／**0.53**／1.03 s | 18.0／**17.7**／28.3 ms | 19.1／21.0 ms | 123.8 s |
| 4 | **69.4**（合計） | 0.27 | 8.39／8.52／11.74 s（`max-num-seqs 2`：第 3、4 條排隊） | 20.8／20.6／24.9 ms | 21.9／28.4 ms | 88.5 s |

**decode-bench**（`scripts/decode-bench.py`，temperature 0，thinking on，各 3 次中位；tool-turn 2.4k prompt／report 1.8k／long-context 8.9k）

| 配方 | tool-turn | report | long-context |
|---|---|---|---|
| 基本 | **50.7** | **69.8** | 44.2 |
| DSpark 推測解碼 | 50.3 | 69.4 | 55.0（輸出品質旗標 broken） |

→ Thor 不用 DSpark。prefix cache 命中 **77%**（vLLM /metrics：hits 6.37M／queries 8.29M）。

## 4. Thor — 資源

| 項目 | 數字 |
|---|---|
| 記憶體 | 整套在用 ≈46 GB／122.8 GB，可用 76–78 GB，無 swap |
| 權重 | NVFP4 17.8 GiB；KV 預留 util 0.25 |
| reranker sidecar | GPU util 0.03，RSS 2.8 GiB |
| 功耗 | 模組 ≈74 W（負載）、GPU 25 W（VDD_GPU）；GPU 57–58 °C |
| 冷啟動 | 一鍵腳本 PREFLIGHT GREEN：服務已在 20 s；vLLM 冷啟約 100 s |

## 5. Spark（開發機）— 夜間大批實驗

| 項目 | 數字 | 來源 |
|---|---|---|
| 全目錄 regression（36 案 ×1） | 自主 **30/32**、越修越壞 0、謊報 0、盲測 tier 17/18 | `logs/regression-20261022-045052` |
| sigstop 重跑（工具層補強後） | 2/2（161 s 中位） | `regression-20261022-071632` |
| 五張牌 k=2 | 5/5，12／18／12 s | `regression-20261022-073412` |
| Gate 2 adaptive 多代理（7 案 ×2） | 12/14、中位 130／132 s（single 54／74 s）、1 謊報 → 關 | `regression-20261022-061854` |
| 社群 RAG 推送 A/B（ops 5 案 ×2） | 開 7/10、關 8/10；兩臂 0／0；10 場 4 次推送、0 次採用 → 閘門重做 | `regression-20261022-081507`、`085256` |
| decode tok/s（tool-turn／report／long） | 基準 51／67／68 → **DSpark 65／100／91**；MTP 同速或略慢 | `decode-bench.py` |
| prefix cache | DSpark k=3 0%、**k=2 31.9%**、k=5 21.9%、MTP 21.6% | `prefix-cache-stats.py` |
| 偵測 → 派工／dashboard 注入 → 結案 | 4.0–4.9 s／14.9–18.4 s（nginx-stopped） | dashboard 實測 |
| 審核代理 | 1.0–1.5 s／次（原 15.3 s），判決一致 5/5 | `REVIEW_THINK=0` |
| 記憶體 | vLLM RSS 3.55→3.69 GiB、rerank 3.34、ops_api 1.15–1.30（Python 159k 索引 2.5 GiB → FTS5 幾 MB）、MemAvailable ≥74 GB | `mem-profile.sh` |

## 6. 知識層（離線評測）

| 指標 | 數字 | 來源 |
|---|---|---|
| 第一階段 recall@20／@50（held-out 1,139 題） | 0.98／0.99 | `kb-rerank-eval.py` |
| 類別代理 category@1 | v1 BM25 0.70 → +rerank 0.775；159k +rerank 0.765 | 同上（類別與真相關只 34% 一致） |
| LLM 判官真相關（held-out 240 題） | 第 1 名對題：BM25 6%、rerank 13%；前 5 有對題 25% | `kb-judge.py` |
| 機房探針 38 句（159k 語料＋rerank＋判官） | 第 1 名對題 **45%**、前 5 **71%** | `kb-judge.py --probes` |
| 修復卡 phrase gate | 漏推 0/8 → 6/8；誤推 3 → 0 | `kb-gate-probe.py` |
| 公開 AIOps 資料集一次性診斷（63 題 held-out，無工具） | Lightning strict 6%／scenario 29%／service 40%；Nano strict 22–24% | `docs/kb-eval*.md`（非本系統用途） |

## 7. 方法聲明

- 每場 drill：`faults.py inject` → sandbox agent 經 MCP 呼叫 ops_api 工具 → `faults.py verify`（兩個 URL 200 且內容正確）；「自主」＝無兜底、無人工；`score.py` 另計越修越壞、謊報、證據先於動作、after-green。
- 舞台事故：watchdog 2 s 巡檢、連兩次異常開事故並派工；秒數＝開事故到兩 URL 綠。
- 推論數字：vLLM nightly 77154ca，Nemotron-3.5-Lightning-30B-A3B-NVFP4，`--kv-cache-dtype fp8`、`--max-num-seqs 2`、`--max-model-len 32768`。
- 所有 log 在各機 `logs/`；Thor 的 regression 目錄名即時間戳。
