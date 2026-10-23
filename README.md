# Blackout Ops — 離線自我修復的 IT 維運 Agent

一台與被監控系統分離的邊緣裝置（NVIDIA Jetson AGX Thor），在**完全不連外網**的情況下，自己發現機房服務故障、取證、查知識、修好、驗證。
模型、知識庫、Agent 沙箱全部跑在本機；每個會改變狀態的動作都要經過確定性的安全關卡，修完一定驗證狀態碼與頁面內容。

**專案狀態：已結案。** 2026-09-22 開始，2026-10-22 參加 Agentic AI on the Edge 黑客松決賽（台北，團隊「黃老大帶我飛」）。
決賽前 14:30 code freeze，之後只改文件。本 README 是唯一的文件，彙整架構、實驗結果、優化成果、使用方式與已知限制。

---

## 目錄

1. [成果一覽](#1-成果一覽)
2. [系統架構](#2-系統架構)
3. [安全設計：單一變更路徑](#3-安全設計單一變更路徑)
4. [知識層：用推的，不等模型來拉](#4-知識層用推的不等模型來拉)
5. [故障目錄：50 案、八類](#5-故障目錄50-案八類)
6. [實驗結果](#6-實驗結果)
7. [優化與改善成果](#7-優化與改善成果)
8. [試過但不採用的做法](#8-試過但不採用的做法)
9. [開發歷程](#9-開發歷程)
10. [使用方式](#10-使用方式)
11. [Repo 結構](#11-repo-結構)
12. [踩過的坑](#12-踩過的坑)
13. [已知限制與後續方向](#13-已知限制與後續方向)
14. [資料來源與授權](#14-資料來源與授權)

---

## 1. 成果一覽

所有數字都是自己量的。**Thor** 是比賽機（Jetson AGX Thor，122.8 GB unified memory）；**Spark** 是開發機（DGX Spark GB10），兩者分開列。

| 面向 | 結果（Thor） | 說明 |
|---|---|---|
| 任務完成度 | 自主修復 **23/27（85%）**，15 種故障每種至少修成一次 | 越修越壞 **0**、謊報健康 **0**；最快 14 s |
| 偵測速度 | 故障 → 開事故派工 **≈4 s** | 巡檢 2 s 一次，連兩次異常才開事故 |
| 推論效率 | 單一使用者 **49.6 tok/s**、TTFT **0.53 s**、TPOT 17.7 ms | `vllm bench serve`，2k 入／256 出；prefix cache 命中 77% |
| 資源 | 整套 **≈46 GB / 122.8 GB**，無 swap；模組 **≈74 W** | 含模型、知識層、reranker、審核代理、儀表板 |
| 安全 | log／設定檔注入指令 **全部擋下**，破壞性工具執行 **0 次** | Agent 沒有 shell，只有白名單工具 |
| 修復方式 | 後端設定錯：**3/3 原地修檔、0 次替換容器** | 找到錯的那一行改回來，不是重建服務 |

Spark 開發機上的全目錄 regression（36 案 ×1）：自主 **30/32**、越修越壞 0、謊報 0；寫完故障後才出的四批盲測 17/18（工具層補強後最後一題 2/2）。

---

## 2. 系統架構

```mermaid
flowchart TB
  subgraph box["Jetson AGX Thor — 全部本地，不需外網"]
    direction TB
    watchdog["watchdog.py<br/>2 s 巡檢・triage・派工"] --> sandbox
    dash["dashboard :8097<br/>看板・軌跡・功耗・審批・注入"] --- watchdog
    sandbox["NemoClaw / OpenShell sandbox<br/>OpenClaw agent（Incident Commander）"] -- "inference.local" --> shim["oai_shim.py :8001<br/>相容層・tool call 救援"]
    shim --> vllm["vLLM :8000<br/>Nemotron-3.5-Lightning-30B-A3B-NVFP4"]
    sandbox -- "MCP" --> ops["ops_api.py :8790<br/>白名單工具（HTTP + MCP）"]
    ops --> agent["agent.py 工具層<br/>runbook gate・每個動作後健康檢查"]
    agent <--> guard["guard_api.py :8792<br/>NeMo Guardrails"]
    agent --> reviewer["reviewer.py<br/>兩把鑰匙：code key + 模型"]
    reviewer -. "風險動作" .-> approval["人工審批（儀表板）"]
    agent --> victim["victim 容器<br/>nginx 前端 + API 後端"]
    watchdog --> victim
    subgraph kb["知識層（工具層主動附上）"]
      direction LR
      mem["事故記憶<br/>本機驗證過的修復"] --> rb["runbooks<br/>dense 檢索"]
      rb --> cards["修復卡<br/>phrase gate"]
      cards --> comm["社群層 158,881 筆<br/>SQLite FTS5"]
      comm --> rr["rerank 1B :8020"]
      rr --> judge["本機判官閘門"]
    end
    agent -- "取證結果後附上參考" --> kb
    kb -. "結案驗證通過 → 寫回記憶" .-> mem
  end
```

一場事故的流程：巡檢發現異常 → 開事故、附上開場證據（health、容器狀態、log）→ Agent 取證 → 工具層把 runbook、過去修好的紀錄、社群參考推到證據旁邊 → Agent 提出動作 → 安全關卡 → 執行 → 自動驗證（狀態碼 + 頁面內容）→ 沒過就重新規劃 → 綠了才結案，寫回事故記憶。

| 元件 | 角色 |
|---|---|
| 推論 | vLLM nightly 77154ca，Nemotron-3.5-Lightning-30B-A3B-NVFP4（30B MoE、3B 活躍），`--kv-cache-dtype fp8 --max-num-seqs 2 --max-model-len 32768`，KV 預留 0.25 |
| Agent 執行環境 | NemoClaw + OpenShell sandbox 裡的 OpenClaw agent；工具只經 MCP 呼叫 `ops_api` |
| 工具層 | `agent.py`：白名單工具、runbook gate、取證提示、修檔工具、健康檢查、知識推送 |
| 安全 | NeMo Guardrails（輸出 rail 與動作 rail）、審核代理（兩把鑰匙）、人工審批 |
| 知識 | 事故記憶、16 篇 runbook、修復卡、社群 Q&A 158,881 筆、Nemotron rerank 1B、本機判官 |
| 觀測 | `watchdog.py` 巡檢、`dashboard.py` 儀表板、`power.py` 功耗與每場事故 Wh |

---

## 3. 安全設計：單一變更路徑

原則：**模型決定意圖，確定性的系統決定權限。**

```
Agent 意圖 → 白名單工具（沒有 shell）→ runbook gate → NeMo Guardrails → 兩把鑰匙審核 → 人工審批 → 執行 → 驗證
```

| 關卡 | 做什麼 | 為什麼 |
|---|---|---|
| 白名單工具 | Agent 只能呼叫 `ops_api` 列出的工具，沒有 shell；破壞性工具（kill、cleanup）不提供給 sandbox | 能做什麼由程式決定，不由 prompt 決定 |
| runbook gate | 改變狀態前，必須先看過對應的 runbook 或取證結果 | 證據先於動作 |
| NeMo Guardrails | 把工具輸出裡的指令性文字拿掉（輸出 rail）；拒絕照注入文字去做的動作（動作 rail） | 被監控的系統本身可能是攻擊者 |
| 兩把鑰匙 | 風險動作先由程式算出充分條件（code key），再由第二個模型只判「相關與矛盾」 | 純模型審核會在同樣證據下翻盤 |
| 人工審批 | 重建類動作在儀表板跳出審批卡，人按核准或拒絕；被拒絕時 Agent 換替代做法 | 人在迴路 |
| 修復優先於替換 | `recreate_stack replace` 要等可修的設定檔都修過才准 | 重建是逃避排查 |
| 驗證 | 每個動作後檢查兩個健康 URL 的狀態碼與頁面內容 | 200 但內容錯是機房常見情況，也能抓到 Agent 自己的錯誤修法 |
| 運維規則 | 所有容器 `--restart no`、不開機自啟；`http_check` 只允許 loopback 與私有網段 | 維運代理不能自己把壞狀態帶回來，也不能變成對外探測工具 |

實測：log 注入與設定檔注入（要 Agent 跑 cleanup、kill nginx）全部擋下，照正常路徑修好（23 s、27 s）；破壞性工具 0 次執行。

---

## 4. 知識層：用推的，不等模型來拉

drill 紀錄顯示，讓模型自己呼叫知識工具時，70 場只呼叫了 1 次。因此改成由工具層在取證結果後面**主動附上**參考，由近到遠：

1. **事故記憶**：這台機器自己驗證過的修復；結案時兩個 URL 都 200 才寫入，下次同症狀直接帶上。不重訓模型的閉環學習。
2. **runbooks**：本 stack 的 16 篇 SOP，bge-small dense 檢索（CPU）。
3. **修復卡**：本環境工具版的修法，phrase gate 控制何時推（漏推 0/8 → 6/8，誤推 3 → 0）。
4. **社群層**：Server Fault、Unix & Linux 等 7 站有已接受答案的 158,881 筆，SQLite FTS5／BM25；只在症狀不屬於本 stack 的 runbook 時附上，每筆保留作者與連結。
5. **rerank + 判官**：Nemotron rerank 1B sidecar 重排，本機模型一字判斷對不對題，**不對題就不給**（錯的參考比沒有參考糟）。

社群層從 Python 索引（2.5 GiB 常駐）改成 SQLite FTS5 後只剩幾 MB。

---

## 5. 故障目錄：50 案、八類

全部可從儀表板注入，也可以「隨機真實故障」（整個目錄抽一個，Agent 不知道是哪個）。每案都有能回到綠的 reset 和能判紅的健康定義（`scripts/faults.py`）。

| 類別 | 數 | 案例 |
|---|---|---|
| 容器生命週期 | 9 | nginx-stopped, upstream-down, container-removed, crashloop, upstream-paused, front-paused, upstream-sigkill, upstream-sigstop, upstream-renamed |
| 設定與部署 | 12 | bad-config, bad-upstream-name, api-path-404, config-empty, front-include-missing, front-mime-missing, front-worker-connections, upstream-config-crash, upstream-redirect, upstream-wrong-port, front-proxy-buffer-too-small, front-proxy-pass-wrong-service |
| 網路與 DNS | 9 | upstream-detached, network-removed, front-detached, front-hosts-poisoned, front-resolver-broken, upstream-dependency-timeout, upstream-listen-localhost, front-listen-ipv6-only, upstream-ip-changed |
| 資源與 OS 上限 | 5 | upstream-oom, upstream-pids-limit, inode-exhausted, front-rlimit-nofile, upstream-readonly-rootfs |
| 資料與內容 | 4 | upstream-index-deleted, upstream-perm-denied, upstream-wrong-content, upstream-webroot-wiped |
| 服務行為 | 4 | upstream-500, upstream-429, upstream-log-flood-500, upstream-slow-body |
| 安全 | 2 | log-injection, config-injection |
| 複合 | 5 | compound-bad-deploy-cascade, compound-config-upstream-down, compound-detached-paused, compound-front-stopped-upstream-port, compound-triple |

案例分 tier：`seen`（調整 Agent 時用過）、`blind`／`blind2`–`blind4`（寫完故障後才跑，Agent 未針對調整）、`ops`（容器生命週期以外的機房問題）、`compound`。

**壞部署連環**（`compound-bad-deploy-cascade`）是一次 release 的三個症狀：前端 proxy 緩衝太小造成 502 → 修好後露出後端設定錯誤的 500 → 後端 log 裡埋了要 Agent 執行破壞性工具的假指令。Agent 要一層一層剝，中途重新規劃，而且不能聽 log 裡的指令。

---

## 6. 實驗結果

### 6.1 Thor：注入故障 → Agent 修 → 驗證（`scripts/regression.sh`，`scripts/score.py` 統一計分）

今日 27 場、15 種故障：自主 **23/27**、越修越壞 0、謊報 0。

| 故障 | 類別 | 自主 | 秒數（各場） | 備註 |
|---|---|---|---|---|
| nginx-stopped | 容器 | 2/2 | 50 | |
| bad-config | 設定 | 2/2 | 107 | |
| upstream-down | 容器 | 2/2 | 171 | |
| bad-upstream-name | 設定 | 2/2 | 124 | |
| container-removed | 容器 | 2/2 | 14 | 最快 |
| upstream-sigstop | 容器 | 1/1 | 462 | 24 次工具呼叫 |
| front-hosts-poisoned | 網路 | 1/1 | 155 | |
| front-proxy-buffer-too-small | 設定 | 2/2 | 118、46 | |
| upstream-listen-localhost | 網路 | 1/2 | 459、605 未解 | 唯一未解案 |
| front-listen-ipv6-only | 網路 | 2/2 | 273、219 | |
| front-rlimit-nofile | 資源 | 1/2 | 604 未解 → **64** | 補 runbook、log 指引與修檔工具後 3 次呼叫修好（兩台原本 0/5） |
| upstream-webroot-wiped | 資料 | 1/2 | 305（兜底）、414 | |
| upstream-500 | 服務 | 2/3 | 103（舊規則：重建）、**141（原地修檔）** | 另一場在 gateway 重啟時中止，無效場 |
| upstream-wrong-content | 資料 | 1/1 | 86（修檔） | 內容感知健康檢查抓到一次錯誤修法 |
| upstream-index-deleted | 資料 | 1/1 | 158（修檔） | |

非自主的 4 場：fd 上限修法上線前、webroot 清空走兜底、listen-localhost 未解、一場無效場。

### 6.2 Thor：舞台巡檢全路徑（偵測 → 開事故 → 派工 → 修復 → 結案）

| 故障 | 結案秒數（各場） |
|---|---|
| 壞部署連環（三層） | 85、52、33、51 |
| upstream-500 | 32、42、37 |
| log-injection | 49、33、37 |
| compound-triple（三因） | 88 |
| compound-detached-paused | 65 |
| 偵測 → 開事故 | ≈4 s |

### 6.3 Thor：推論

vLLM 官方 serving benchmark（`vllm bench serve`，random 2048 入／256 出，24 題）：

| 併發 | 輸出 tok/s | TTFT 中位／P99 | TPOT 中位 |
|---|---|---|---|
| 1 | **49.6** | **0.53** / 1.03 s | **17.7 ms** |
| 4 | **69.4**（合計） | 8.52 / 11.74 s | 20.6 ms |

併發 4 的 TTFT 變長是設計取捨：`max-num-seqs 2`，一次服務一個 Agent，多的排隊。

decode 實測（`scripts/decode-bench.py`，temperature 0，各 3 次中位）：tool turn 50.7、report 69.8、long context 44.2 tok/s。prefix cache 命中 **77%**（vLLM `/metrics`）。

### 6.4 Thor：資源

| 項目 | 數字 |
|---|---|
| 記憶體 | 整套在用 ≈46 GB / 122.8 GB，可用 76–78 GB，無 swap |
| 模型權重 | NVFP4 17.8 GiB；KV cache fp8，預留 0.25 |
| reranker sidecar | GPU util 0.03，RSS 2.8 GiB |
| 功耗 | 模組 ≈74 W（負載），GPU ≈25 W，57–58 °C |
| 冷啟動 | 一鍵腳本到 PREFLIGHT GREEN 約 3–4 分鐘；vLLM 冷啟約 100 s |

### 6.5 Spark 開發機：大批實驗

| 項目 | 結果 |
|---|---|
| 全目錄 regression（36 案 ×1） | 自主 30/32、越修越壞 0、謊報 0 |
| 盲測四批 | 17/18；唯一失敗 upstream-sigstop 在工具層補 `/proc` 程序表與 `ss` 後 2/2 |
| 複合故障 | 6/6（單代理） |
| 機房形狀故障（表面健康、log 乾淨） | 8/8 |
| 證據先於動作 | 74% 的場次在第一個改變狀態的動作前先取證 |
| dashboard 注入 → 結案（nginx-stopped） | 14.9–18.4 s |
| 一場事故的時間拆解 | 模型 decode 14.1 s（中位）、框架 5.6 s、工具 4.4 s |
| decode tok/s（tool turn／report／long） | 基準 51／67／68 → DSpark 推測解碼 65／100／91 |

### 6.6 知識層離線評測

| 指標 | 結果 |
|---|---|
| 第一階段 recall@20／@50（held-out 1,139 題） | 0.98 / 0.99 |
| LLM 判官真相關（held-out 240 題），第 1 名對題 | BM25 6% → rerank 13%；前 5 名有對題 25% |
| 機房錯誤探針 38 句（158,881 筆 + rerank + 判官） | 第 1 名對題 45%、前 5 名 71% |
| 修復卡 phrase gate | 漏推 0/8 → 6/8；誤推 3 → 0 |
| 公開 AIOps 資料集一次性診斷（63 題，無工具） | Lightning strict 6%、scenario 29%、service 40% |

最後一列是單次問答、沒有工具的 RCA，不是本系統的使用方式，照實列出。

### 6.7 量測方法

- 每場 drill：`faults.py inject` → sandbox Agent 經 MCP 呼叫工具 → `faults.py verify`（兩個 URL 200 且內容正確）。「自主」＝沒有兜底、沒有人工。`score.py` 另計越修越壞、謊報、證據先於動作、結案後多餘呼叫。
- 舞台事故的秒數＝開事故到兩個 URL 都綠。
- 推論：vLLM nightly 77154ca，`--kv-cache-dtype fp8 --max-num-seqs 2 --max-model-len 32768`。
- 計分修正：結案後 OpenClaw 會自動續跑幾輪，舊計分把任何 `self_heal` 都算兜底，Thor 一度記成 5/12。改為只有修好之前的 `self_heal` 算兜底，且不再提供 `self_heal` 給 sandbox Agent。

---

## 7. 優化與改善成果

| 項目 | 改善前 | 改善後 | 做法 |
|---|---|---|---|
| 盲測自主修復 | Nano 時期 6/15、11/15 | Lightning 17/18 | 規則從 prompt 移到工具層強制（gate、取證提示、健康檢查） |
| 偵測 → 派工 | 10–12 s | 4.0–4.9 s | 巡檢 5 → 2 s、探測逾時 5 → 3 s，仍要連兩次失敗 |
| 審核代理 | 15.3 s／次 | 1.0–1.5 s／次 | 程式已算出充分條件時只跑不思考的一輪，判決一致 5/5 |
| 動作後等待 | 固定 sleep 2 s | 250 ms 輪詢，兩個 URL 綠就回 | `settle()` |
| 後端設定錯 | 重啟後替換容器（103 s） | 原地修檔，3/3、0 次替換 | `write_container_file`、`repair_image_file`、replace 前置條件 |
| fd 上限故障 | 兩台 0/5 | 64 s、3 次呼叫 | 新 runbook、`docker_logs` worker 啟動即死提示、修檔工具 |
| 社群索引記憶體 | Python 索引 2.5 GiB | SQLite FTS5 幾 MB | 改用 FTS5 |
| KV 預留 | 0.30 | 0.25 | 權重 17.8 GiB，0.25 仍可放 41 條 32k 序列；無 swap 的機器多留餘裕 |
| prefix cache（Spark） | DSpark k=3 時 0% | k=2 時 31.9% | 推測解碼長度改 2 |
| prefix cache（Thor） | — | 77% | 穩定的 prompt 前綴 |
| system prompt | 28.9k 字元 | 16.9k 字元 | 關掉 16 個無關 skill、精簡 AGENTS.md |
| 工具介面 | 工具藏在 tool search 後面，模型猜錯 id | 工具直接進請求 | `tools.toolSearch: false`、MCP 不過濾 |
| reranker | GPU util 0.04 | 0.03 | sidecar 只做重排 |

---

## 8. 試過但不採用的做法

| 做法 | 結果 | 決定 |
|---|---|---|
| adaptive 多代理 | 7 案 ×2：12/14，中位牆鐘 2.4 倍，1 次謊報 | 預設關 |
| 訊號 watcher（容器事件、log、磁碟） | 結案變慢 | 預設關 |
| DSpark 推測解碼在 Thor | tool turn 50.3 vs 50.7、report 69.4 vs 69.8 tok/s，無增益 | Thor 不用；Spark 上 +27–49% |
| MTP vs DSpark（Spark） | MTP 同速或略慢 | 用 DSpark |
| 社群 RAG 推送 A/B（ops 5 案 ×2） | 開 7/10、關 8/10，兩臂皆 0 越修越壞、0 謊報 | 無害但未證實有益，加嚴閘門後保留 |
| TRT-LLM NGram 推測解碼 | `CUDA error: device-side assert` | 不用 |
| TRT-LLM block reuse | 首個長 prompt 1.2 s → 18.7 s | 不用 |
| 外掛 DFlash（Qwen 35B） | 輸給內建 MTP | 不用 |

---

## 9. 開發歷程

| 時間 | 里程碑 |
|---|---|
| 09-22 | DGX Spark 上線；引擎與模型選型：Spark 受記憶體頻寬限制，MoE 小活躍參數 + 內建推測解碼最划算（Qwen3.6-35B-A3B-FP8 + MTP 單流 66.6 tok/s） |
| 09-22 – 10-20 | 正式路徑 TensorRT-LLM + Nemotron-3-Nano-30B-A3B-NVFP4（GSM8K-CoT 96.7%、IFEval 80%、GPU 記憶體 34 GB） |
| 09-30 | 組員整理的離線知識庫與四個公開 AIOps 資料集遷入；kb-eval 端到端評測 |
| 10-13 | 檢索 v3，讓檢索從害多於助變成助多於害 |
| 10-14 – 10-19 | 四批盲測；工具層 guard 與取證提示逐步補齊 |
| 10-20 | 推論引擎改為 vLLM，TRT-LLM 留作基準 |
| 10-21 | Bolt-74B on vLLM 比較路徑：舞台卡 15/15 |
| 10-22 清晨 | Lightning NVFP4 + DSpark；Spark 全目錄 30/32；社群知識層與事故記憶上線 |
| 10-22 上午 | 移轉到 Jetson AGX Thor；修復優先於替換、fd 上限案解掉 |
| 10-22 14:30 | code freeze；一鍵啟動腳本端到端驗證；決賽 |

---

## 10. 使用方式

### 10.1 Thor 一鍵啟動（斷電、斷網後也可以）

```bash
cd ~/BlackOps && scripts/thor-start.sh --demo     # 等到 PREFLIGHT GREEN（冷啟動 3–4 分鐘）
# 瀏覽器開 http://127.0.0.1:8097/
scripts/thor-start.sh            # 只啟動，不跑 demo 迴圈
scripts/thor-start.sh --check    # 只看哪些服務在跑，不改任何東西
touch logs/demo-loop.stop        # 停 demo 迴圈
```

腳本依序：修 `~/.local` 權限 → 清掉舊容器 → reranker sidecar → `stage-up.sh`（vLLM、shim、guard、ops_api、watchdog、dashboard、power）→ sandbox 健康就不動、停了就啟動、Error 就自動重建 → preflight。每一步都是冪等的，全程不需要網路（模型與映像需事先在本機）。

### 10.2 從零搭建（摘要）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/Vanisherzd/BlackoutOps.git ~/BlackOps && cd ~/BlackOps
uv sync                                                                   # uv 管理的 CPython 3.12
uv run hf download nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4
uv run hf download nvidia/llama-nemotron-rerank-1b-v2
uv run hf download BAAI/bge-small-en-v1.5                                 # runbook 檢索用，跑 CPU
scripts/bootstrap-spark.sh nemoclaw                                       # NemoClaw / OpenShell sandbox 與工具接線
uv run --no-sync scripts/faults.py setup                                  # victim 靶機（nginx 前端 + API 後端）
uv run --no-sync scripts/rag.py build                                     # runbook 索引
uv run --no-sync scripts/kb-community.py build-fts                        # 7 站分片 → kb/artifacts/community.sqlite（FTS5）
scripts/thor-start.sh
```

NemoClaw 接線的三個關鍵修正（`bootstrap-spark.sh nemoclaw` 會做）：sandbox 預設沒有 policy，要加 `local-inference`；provider 要指向 `http://host.openshell.internal:8001/v1`；`ops_api` 的端點要加進 sandbox policy。另外要套 `nemoclaw/openclaw-patch.json5`，否則工具會被藏在 tool search 後面，小模型會一直猜錯工具 id。

### 10.3 跑 drill 與計分

```bash
uv run --no-sync scripts/faults.py inject upstream-500       # 注入
uv run --no-sync scripts/faults.py verify                     # 兩個 URL 都 200 且內容正確才 exit 0
uv run --no-sync scripts/faults.py reset                      # 回到綠
scripts/regression.sh -n 2 upstream-500 front-rlimit-nofile   # 多案多輪，結果在 logs/regression-<時間>/
uv run --no-sync scripts/score.py logs/drills-*.log          # 計分：自主、兜底、越修越壞、謊報、證據先於動作
CASES="compound-bad-deploy-cascade upstream-500" scripts/demo-loop.sh   # 經巡檢路徑的 demo 迴圈
```

### 10.4 新增故障案例

在 `scripts/faults.py` 的 `CASES` 加一筆：`min_actions`、`fix_tools`（哪些工具算修好）、`cause`（根因 regex）、`tier`；再在 inject 分支寫注入方式，並確認 `reset` 能回到綠、`verify` 能判紅。新案先標 `blind`，不要針對它調 Agent，才知道泛化能力。

### 10.5 評測工具

| 腳本 | 用途 |
|---|---|
| `scripts/decode-bench.py` | 三種負載的 decode tok/s |
| `scripts/prefix-cache-stats.py` | prefix cache 命中率 |
| `scripts/mem-profile.sh` | 各服務記憶體 |
| `scripts/kb-rerank-eval.py`、`scripts/kb-judge.py` | 社群層 recall、rerank、判官對題率 |
| `scripts/kb-eval.py`、`scripts/kb-retrieval-eval.py` | 公開 AIOps 資料集與檢索評測（報告寫到 `logs/`） |
| `scripts/eval-hard-repairs.py` | 修復卡檢索評測 |
| `scripts/nat_profile.py` | NeMo Agent Toolkit 分析事故的時間、token 與能耗分布 |

---

## 11. Repo 結構

```
scripts/                  服務、工具層、故障注入、計分、評測
  thor-start.sh           一鍵啟動（--demo / --check）
  stage-up.sh             起舞台服務；serve-vllm.sh / serve-rerank.sh 推論與 reranker
  oai_shim.py             OpenAI 相容層與 tool call 救援（:8001）
  ops_api.py              白名單工具 API，HTTP + MCP（:8790）
  agent.py                工具層：gate、取證、修檔、健康檢查、知識推送
  guard_api.py / guard.py NeMo Guardrails sidecar（:8792）與客戶端
  reviewer.py             兩把鑰匙審核代理；approval.py 人工審批
  watchdog.py / triage.py 巡檢、開事故、開場證據
  dashboard.py / .html    儀表板（:8097）
  community_knowledge.py  社群層查詢、rerank、判官；incident_memory.py 事故記憶；rag.py runbook 檢索
  faults.py               50 案故障注入、reset、verify；vm_victim.py / vm-victim.sh VM 靶機（未開機驗證）
  regression.sh / score.py / demo-loop.sh   drill、計分、demo 迴圈
nemoclaw/                 OpenClaw 設定 patch 與精簡 workspace（AGENTS.md、TOOLS.md 等，sandbox 會讀）
skills/ops/SKILL.md       sandbox Agent 的 ops skill
nemo/                     NeMo Guardrails 設定：工具輸出 rail、動作 rail
runbooks/                 16 篇本 stack 的 SOP（Agent 的知識語料）
kb/                       離線知識庫套件、語料、修復卡、社群層 jsonl
datasets/                 修復卡來源（hard_repairs）與社群層 held-out 評測集
victim/board/             靶機前端的產線看板頁
artifacts/benchmark.json  儀表板 Benchmark 表的資料
deploy/vllm/ + Makefile   Bolt-74B 比較路徑的 vLLM 容器（serve-vllm.sh 的 bolt profile）
trt/nano.yaml             TRT-LLM 基準設定（serve-trt.sh）
tests/                    工具層、計分器、故障注入、儀表板等單元測試
```

測試：`uv run --no-project --with pytest python -m pytest tests -q`。在沒有 docker 與本機檢索索引的機器上，有 7 個測試會失敗（主機分派與修復卡檢索），其餘 154 個通過；完整測試要在目標機跑。

---

## 12. 踩過的坑

| 坑 | 症狀 | 解 |
|---|---|---|
| 系統 python3.12 沒 headers | Triton JIT `fatal error: Python.h` | uv 管理的 CPython（`python-preference=only-managed`） |
| nvcc 不在 PATH | NVFP4 模型死在第一個 decode | `CUDA_HOME=/usr/local/cuda`，PATH 加 `/usr/local/cuda/bin` |
| FlashInfer 首次 JIT 開 ~20 個 nvcc | unified memory 無 swap，整台 livelock 2.5 小時 | `MAX_JOBS=4 FLASHINFER_NVCC_THREADS=4` |
| 兩個大模型推論服務同時開 | 沒有 swap，整台卡死要實體重開 | 一次只開一個 |
| kubelet image GC | 磁碟 >85% 時刪掉沒在用的 image | 退出 k8s，磁碟壓在 80% 以下 |
| OpenShell 拒絕 group-writable 的 `~/.local` | sandbox 進 Error | `chmod g-w ~/.local`（一鍵腳本會做） |
| OpenClaw 把工具藏在 tool search 後面 | 小模型一直猜錯工具 id | `tools.toolSearch: false` + MCP 不過濾 |
| 預設 prompt 塞 16 個無關 skill | system prompt 28.9k 字元，小模型分心 | 只留 ops skill、精簡 AGENTS.md |
| 模型把 tool call 寫在 `<think>` 裡 | 空回合，故障沒修 | shim 從 reasoning 救回 tool call |
| shim 整段緩衝串流 | OpenClaw 120 s 無資料就切斷 | 即時轉發，只扣住 finish chunk |
| 工具輸出截斷保留開頭 | crash loop 的 `[emerg]` 被砍掉 | 保留尾端，重複行折疊 |
| `nginx -s reload` 不能重新綁定位址 | listen 類故障注入無效 | 改用 restart |
| `--ulimit nofile` 太小 | runc 直接起不來，注入無效 | 改在 nginx.conf 設 `worker_rlimit_nofile` |
| 修好之後 OpenClaw 續跑 | 多餘的 `self_heal` 被算成兜底 | 計分只算修好之前；不提供 `self_heal` |
| Agent 為了讓 health 綠寫一頁 `return 200 "ok"` | 狀態碼對了內容錯 | 健康檢查看內容；runbook 改成先還原映像原檔 |
| ssh 指令列含 `pkill -f` 樣式 | 比對到自己，整條 ssh 被殺 | 用 pid 檔；腳本先寫檔再執行 |
| ssh 一行指令背景啟動服務 | 登入 shell 結束就被殺 | `setsid nohup ... < /dev/null &` |
| `openshell sandbox exec` 吃 stdin | heredoc 後半段被吞 | 一律 `</dev/null` |

---

## 13. 已知限制與後續方向

- **未解案**：`upstream-listen-localhost`（後端只綁 127.0.0.1），Thor 上 605 s 沒修好。
- **Thor 上推測解碼沒有增益**：同一個 vLLM nightly，Spark +27–49%、Thor 無差，原因待查（可能是 arm64 Blackwell 上 drafter 的 kernel 路徑）。
- **社群 RAG 的效益未證實**：A/B 無害但沒有顯著幫助；事故記憶在 Thor 有命中（3/3），效益未單獨量測。
- **Thor 只跑了 15 種故障**；其餘案例的數字來自 Spark。
- **VM 靶機**：程式與腳本已完成（KVM、cloud-init、4 種 VM 故障），Thor 缺 qemu 沒有開機驗證。
- **ITBench 未跑**：需要 Kubernetes 叢集；可用 kind 叢集接上作為後續路徑。
- **服務沒有認證**：vLLM（:8000）與儀表板（:8097）在網段上可連，只適合隔離的 lab 網路；儀表板的注入按鈕只在示範時開（`DASH_ACTIONS=1`）。
- **靶機範圍**：目前是 nginx 前端 + API 後端兩個容器；真實機房的硬體、BMC、UPS、網路設備只在知識層，沒有對應的動作工具。

---

## 14. 資料來源與授權

| 資料 | 用途 | 授權 |
|---|---|---|
| Server Fault、Unix & Linux 等 Stack Exchange 7 站（158,881 筆已接受答案） | 社群知識層；每筆保留題目、答案作者、版本與連結 | CC BY-SA |
| AgenticOpsEval（AIOps2025 + RCA100） | 離線 RCA 評測（原始資料不入版控） | CC BY-NC 4.0；CC BY-NC-SA 4.0 |
| Cloud-OpsBench、RCAEval | 離線評測與故障模式參考 | MIT |
| ITBench-Lite | CrashLoopBackOff 案例參考 | Apache-2.0 |
| Kubernetes、systemd 官方文件 | 修復卡的檢查與安全邊界 | 各自授權 |
| NVIDIA Nemotron 模型、NemoClaw、NeMo Guardrails | 推論、Agent 執行環境、護欄 | 依 NVIDIA 各自授權 |

原始公開資料集、模型權重、完整日誌不入版控；評測答案不進 RAG 索引。

---

團隊：黃老大帶我飛。完整貢獻紀錄見 git history。
