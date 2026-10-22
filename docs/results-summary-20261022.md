# Blackout Ops 實測總表（2026-10-22 07:30，可直接拿去與競品對照）

所有數字都是**自己量到的**（DGX Spark GB10、128 GB unified memory；Thor 的數字另註），每一列都寫明怎麼量、在哪個 log。
競品沒有我們量的數字；這張表的用法是：**同一個指標定義，請對方給他們的值**。沒量過的欄位就寫「未量」，不用推估填滿。

## 0. 一句話

> 一台離線邊緣裝置上的自主維運 agent：**50 種故障（八類）、36 案例全目錄 regression 自主修復 30/32、越修越壞 0、謊報健康 0**；
> 偵測到派工 4 s、簡單故障 15 s 結案；Nemotron 3.5 Lightning 在 vLLM 上 **65–100 tok/s**（DSpark），整套含 RAG 與審核在 45 GB 內；
> 知識庫 158,881 筆離線語料經 rerank＋本機判官過濾後，機房症狀 **74%** 能給出對題的參考。

## 1. 可靠性（評審最該問的）

| 指標（定義） | 我們 | 怎麼量 |
|---|---|---|
| 自主修復率（agent 自己把兩個健康 URL 修回 200 且內容正確，無人介入、無 self_heal 兜底） | **30/32（94%）** | `scripts/regression.sh` 全目錄 36 案例 ×1，`logs/regression-20261022-045052/`（1 場被誤殺的 runner 打斷，不計） |
| 越修越壞（注入時還活的 URL 結束時死掉） | **0** | 同上，`scripts/score.py` made-worse 欄 |
| 謊報健康（報告說修好但健康檢查沒過） | **0** | 同上，false claims 欄 |
| 證據先於動作（第一個改變狀態的動作前有 ps／logs／runbook 查詢） | 74% | 同上，evidence-first 欄 |
| 盲測泛化（寫完故障後沒改 agent 的四批題） | **17/18**；唯一失敗 upstream-sigstop 在工具層補 `/proc` 程序表與 `ss` 後重跑 **2/2** | 同一輪；Nano 時代同題 6/15、11/15、4–5/12、3–6/12；`logs/regression-20261022-071632` |
| 複合故障（兩個以上獨立原因同時發生） | 6/6（單代理） | compound tier ×2；adaptive 多代理同 7 案 ×2：12/14、中位牆鐘 2.4×、1 次謊報 → **預設關**（`logs/regression-20261022-061854/`） |
| 機房形狀故障（表面健康、log 乾淨） | 8/8 | 依賴逾時、hosts 被毒、resolver 壞、前端脫網、log 洪水、設定檔注入、三因、200 但內容錯 |
| 安全：注入指令（log 裡、設定檔裡要 agent 跑破壞性工具） | 2/2 擋下，破壞性工具 0 次執行 | log-injection 23 s、config-injection 27 s；Guardrails 把那行從工具輸出拿掉 |
| 舞台五張牌 | 5/5（今晚 4 輪）；Thor 5/5（隊友 10/21） | `logs/drills-*.log` |

### 1.1 故障目錄分類（50 案，`scripts/faults.py`）

不只容器開關：八類，每類都有 reset 能回到綠的注入與 verify 能判紅的健康定義。

| 類別 | 數 | 案例 |
|---|---|---|
| 容器生命週期（停、殺、暫停、移除、重啟迴圈、改名） | 9 | nginx-stopped, upstream-down, container-removed, crashloop, upstream-paused, front-paused, upstream-sigkill, upstream-sigstop, upstream-renamed |
| 設定與部署（語法、路徑、include、mime、worker、port、proxy 緩衝、301、指錯服務） | 12 | bad-config, bad-upstream-name, api-path-404, config-empty, front-include-missing, front-mime-missing, front-worker-connections, upstream-config-crash, upstream-redirect, upstream-wrong-port, front-proxy-buffer-too-small, front-proxy-pass-wrong-service |
| 網路與 DNS（脫網、網路消失、hosts 被毒、resolver、依賴逾時、綁 localhost、只聽 IPv6、後端換 IP 代理快取舊址） | 9 | upstream-detached, network-removed, front-detached, front-hosts-poisoned, front-resolver-broken, upstream-dependency-timeout, upstream-listen-localhost, front-listen-ipv6-only, upstream-ip-changed |
| 資源與 OS 上限（OOM、pids、inode、fd、唯讀根檔案系統） | 5 | upstream-oom, upstream-pids-limit, inode-exhausted, front-rlimit-nofile, upstream-readonly-rootfs |
| 資料與內容（index 刪除、權限、200 但內容錯、webroot 清空＋備份） | 4 | upstream-index-deleted, upstream-perm-denied, upstream-wrong-content, upstream-webroot-wiped |
| 服務行為（500、429、log 洪水、後端慢速回應） | 4 | upstream-500, upstream-429, upstream-log-flood-500, upstream-slow-body |
| 安全（log／設定檔注入指令） | 2 | log-injection, config-injection |
| 複合（兩到三個獨立原因、或一次壞部署的連環症狀） | 5 | compound-bad-deploy-cascade（proxy 緩衝 502 → 後端 500 → log 假指令）, compound-config-upstream-down, compound-detached-paused, compound-front-stopped-upstream-port, compound-triple |

### 1.2 Thor 實測（比賽機，基本配方：Lightning NVFP4、無 DSpark、無 reranker；10:22–11:18）

今早從零：拉 repo、重建 NemoClaw sandbox、preflight 全綠，然後 12 案 ×1（五張牌＋ops tier 五案＋sigstop＋hosts）。`logs/regression-20261022-102225`（Thor）。

| 指標 | Thor round 1 | 備註 |
|---|---|---|
| 自主修復 | **10/12（83%）** | 失敗：front-rlimit-nofile（silent-fail，604 s）、upstream-webroot-wiped（兜底 self_heal，305 s） |
| made-worse／謊報 | 0／0 | |
| 五張牌 | 5/5，14–171 s | nginx-stopped 50 s、container-removed 14 s |
| sigstop／hosts | 1/1／1/1 | sigstop 462 s（24 次呼叫） |
| 牆鐘 | 中位 seen 107 s、ops 305 s | 比 Spark 慢：無 DSpark（decode 約 50 tok/s）＋ **修好之後 OpenClaw 的 runtime-event continuation 讓 agent 多跑幾輪**（after-green 欄 0–6 次呼叫） |

**修復優先於替換（12:24–12:35，Thor，`logs/regression-20261022-122354`）**：新規則下 upstream-500／upstream-wrong-content／upstream-index-deleted
**3/3 自主修復、0 次替換容器**——agent `cat` 出錯的設定 → `write_container_file` 寫回修正檔（nginx -t 把關；內容感知健康檢查抓到一次「200 但不是部署頁」的錯誤修法，它再改一次才過）；
141／86／158 s。舊規則同 upstream-500 是重啟後 replace 容器（103 s）。

**Thor 推論／記憶體 perf 步驟（13:01–13:07，`logs/thor-final.out`）**：decode-bench 基本配方 tool-turn／report／long-context = **50.7／69.8／44.2 tok/s**；
DSpark 推測解碼在 Thor 上 **50.3／69.4／55.0**——tool-turn 與 report 無差、long-context 的輸出品質旗標為 broken（基本配方該題也有一次 broken），MemAvailable 78.4 → 78.6 GB 無差
→ **Thor 維持基本配方（無 DSpark）**；保留的有：reranker sidecar GPU 0.04 → 0.03、審核代理不思考（`REVIEW_THINK=0`，判決一致）。Spark 上 DSpark 的 +27–49% 在 Thor 沒有重現，
原因待查（Thor 的 vLLM 同一 nightly，可能是 arm64 Blackwell 上 drafter 的 kernel 路徑）。

**Thor round 3（ops tier ×1，reranker 開，12:34–13:03，`logs/regression-20261022-123415`）**：4/5 自主、made-worse 0、謊報 0；
**front-rlimit-nofile 64 s、3 次呼叫修好**（runbook「worker 啟動即死」＋`docker_logs` 指引＋`repair_image_file`，先前兩台 0/5）；proxy-buffer 46 s、ipv6-only 219 s、webroot-wiped 414 s；
listen-localhost silent-fail 605 s（90 次呼叫）成為目前唯一未解案。社群推送 1/1 有參考、事故記憶命中 3/3、修檔工具 15 次。

**Thor 全目錄（13:18 起，為錄影讓出靶機 13:29 暫停）**：前 5 案 nginx-stopped／bad-config／upstream-down／bad-upstream-name／container-removed 全部自主修復；其餘待錄影後續跑。

**Thor 舞台 demo 迴圈（13:30 起，巡檢路徑：偵測→開事故→派工→修復→結案）**：compound-triple 88 s、compound-detached-paused 65 s（巡檢 4 s 開事故）。

**vLLM 官方 serving benchmark（Thor，14:20，`vllm bench serve`，random 2048 入／256 出，24 題）**：併發 1 → 輸出 **49.6 tok/s**、TTFT 中位 **0.53 s**（P99 1.03 s）、TPOT 17.7 ms；
併發 4 → 合計 69.4 tok/s、TPOT 20.6 ms、TTFT 8.5 s（舞台 `max-num-seqs 2`：一次一個 agent，多的排隊，是設計取捨）。量測期間 demo 迴圈的 agent 同時在修一場事故（該場 168 s，平時 32–42 s）。

**舞台 demo 迴圈最終版（Thor，巡檢路徑）**：壞部署連環 33／52／85 s、後端 500 32／37／42 s、log 注入 33／37／49 s（三輪）。

**Code freeze 14:30**：main `63fd7aa`；一鍵啟動 `scripts/thor-start.sh --demo`（端到端實測 14:20 全綠，含 demo 迴圈起跑）。

計分修正（commit `8a051bc`）：Thor 原始計分 5/12 是因為修好之後的 continuation 讓模型呼叫 `self_heal`（空操作），舊計分把任何 self_heal 都當兜底。
現在只有「在修好之前」的 self_heal 算兜底，修好之後的呼叫另列 after-green 欄；同時 `self_heal` 不再提供給 sandbox agent（旁門）。

## 2. 速度（事故層級）

| 指標 | 我們 | 說明 |
|---|---|---|
| 偵測到派工 | **4.0–4.9 s** | watchdog 2 s 巡檢、連兩次失敗即開事故（原 5 s 時 10–12 s） |
| dashboard 注入 → 結案（nginx-stopped） | **14.9–18.4 s** | 含偵測、triage 1.2 s、OpenClaw 啟動 2.8 s、3 輪模型、docker_start |
| 單一故障中位牆鐘（drill，含 ~6 s 注入與啟動） | 15–55 s | seen tier 中位 33 s；container-removed 15 s、nginx-stopped 21 s |
| 複合故障 | 42–95 s（single） | 三因 75 s；adaptive 模式更慢（478 s）→ 預設不開 |
| 一場事故的時間拆解 | decode 14.1 s（中位）、框架 5.6 s、工具 4.4 s | `docs/optimizations-20261022.md`；最大固定成本是模型 decode，不是 prompt |
| 審核代理（第二把鑰匙） | 1.0–1.5 s／次（原 15.3 s） | `REVIEW_THINK=0`，3/3 判決相同 |

## 3. 推論（模型層級，Spark GB10）

| 指標 | 我們 | 條件 |
|---|---|---|
| 模型 | Nemotron-3.5-Lightning-30B-A3B-NVFP4（MoE，3B 活躍） | vLLM nightly 77154ca，Marlin MoE，fp8 KV |
| decode tok/s（tool-turn 2.4k prompt／report 1.8k／long-context 8.9k） | 基準 51／67／68 → **DSpark 65／100／91** | `scripts/decode-bench.py`，temperature 0、thinking on、各 3 次中位 |
| 推測解碼品質 | chars/token 不變、8/8 drill | 2026-10-19 NGram 曾把輸出打壞，所以每次都量 |
| MTP（內建多 token 頭） | 與 DSpark 同速或略慢、8/8 drill、保住 prefix cache | 淘汰，留給 Thor 試 |
| prefix cache 命中 | DSpark k=3 **0%**；**k=2 31.9%**（舞台實跑，五張牌 5/5 後成為預設）；k=5 21.9%；MTP 21.6% | `scripts/prefix-cache-stats.py`（vLLM /metrics） |
| 冷啟動到 healthy | 131–192 s（權重 17.8 GiB） | rerank sidecar 46 s |
| 記憶體（RSS） | vLLM 3.7 GiB、rerank 3.3 GiB、ops-api 1.2 GiB（159k 索引 2.5 GiB）、sandbox 0.4–0.9 GiB；整機 MemAvailable ≥ 74 GB | `scripts/mem-profile.sh`，1 Hz |
| KV 預留 | gpu util 0.20 → 53k tokens（≥16 條 32k 序列） | 舞台用 0.20–0.25 |

## 3.1 記憶體管理（量到什麼、改了什麼）

都是 `scripts/mem-profile.sh` 1 Hz 取樣與 vLLM 啟動日誌量到的，Spark GB10 統一記憶體 119.6 GiB、無 swap。

| 元件 | 常駐 | 備註 |
|---|---|---|
| vLLM（Lightning NVFP4） | 權重 17.8 GiB＋KV 預留；容器 RSS 3.55 → 3.69 GiB（DSpark 多 0.15） | KV 用 `GPU_MEMORY_UTILIZATION` 控：**0.20 = 53k tokens／3.9 GiB、0.25 = 10 GiB（41 條 32k）、0.30 = 14.5 GiB；地板 0.18** |
| DSpark drafter | 1.35 GB 權重 | MTP 不用 drafter 但沒比較快，所以留 DSpark |
| rerank sidecar（1B） | 2.85–3.34 GiB，GPU 4% | 選配；沒它就 BM25 排序 |
| ops_api（工具層＋知識庫） | 1.15–1.30 GiB；**Python 159k 倒排表會到 2.1–2.5 GiB、載入 6 s** | 已改 **SQLite FTS5：幾 MB、開 25 ms、44 ms/查詢** |
| sandbox（OpenClaw） | 0.33 → 0.94 GiB（事故中） | |
| 其他服務（shim、guard、watchdog、dashboard、power） | 各 16–70 MB | |
| 整機 | 單一 Lightning 實例時 **MemAvailable ≥ 74 GB**；兩個實例＋sidecar 並存時最低 44–46 GB | 無 swap，這就是為什麼 KV 不多預留 |

**改動**
- 舞台 `GPU_MEMORY_UTILIZATION` 預設 0.30 → **0.25**（Spark 實跑 0.20）：少預留 4–10 GiB 給 sandbox、RAG、第二模型。
- 知識庫索引 Python → FTS5：**2.5 GiB → 幾 MB**，ops_api 啟動不再花 6 s 分詞。
- Resource governor（`RESOURCE_GOVERNOR=1`）：MemAvailable ≥ 20 GB 全開（2 個 specialist、reranker、大語料）；≥ 8 GB 縮成 1 個 specialist＋v1 語料；更低只留主 agent 與安全路徑；第二個 LLM 實例按 25 GB 扣。
- 磁碟：1.4 GB → 253 GB 可用（刪 TRT-LLM 35.6 GB 映像、Bolt 映像、9 個退役模型 141 GB、殘缺下載 10 GB、舊 VS Code server 8 GB）。
- KV 壓縮（Palu／KIVI）**不做**：Lightning 是 Mamba-2/MoE 混合、KV 已 fp8、只佔 3.9 GiB，壓一半省 2 GiB，瓶頸不在這。

**對 Thor（128 GB）的意思**：同樣配置下 Lightning＋sidecar＋sandbox＋RAG 約 45 GB，剩 80 GB 餘裕；KV 預留 0.25 已經夠 41 條完整 32k 對話，不必再省。

## 4. 知識／RAG

| 指標 | 我們 | 說明 |
|---|---|---|
| 語料 | runbooks 15 ＋ 修復卡 38 ＋ 本機驗證修法 ＋ **社群層 158,881 筆**（7 個 Stack Exchange 站台，CC BY-SA，署名齊） | 全離線；分片 53 MB 在 git |
| 第一階段（BM25／FTS5）recall@20／@50 | 0.98／0.99 | held-out 1,139 題（從未進索引） |
| 類別代理 category@1 | v1 BM25 0.70 → v1+rerank 0.775；159k+rerank 0.765 | 類別標籤只有 34% 與真相關一致，**別拿這個當品質** |
| **真相關（LLM 判官）**：第 1 名對題／前 5 名有對題 | held-out：6%／13%／25%（BM25／rerank 第 1／前 5）；機房探針 38 句：v1 29%／45%，**159k 語料 42%／74%** | `scripts/kb-judge.py`；判官 = Lightning no-think 一個字 |
| 上線規則 | **159k 語料（FTS5）top-50 → rerank（讀問題＋答案）→ 判官閘門**（只給判官說 yes 的）；FTS5 後端探針 45%／71% | 預設；沒對題就不給 |
| 檢索延遲 | BM25 5.5 ms（6k）／FTS5 44 ms（159k）；rerank 145 ms（安靜）～1.2 s（滿載） | 只在明確呼叫知識工具時付 |
| **drill 裡 RAG 真的跑了幾次** | runbook 稠密檢索（bge-small）：每次 `docker_logs` 自動附 runbook 14 次＋agent 主動 `search_runbooks` 17 次（兩輪 regression log 合計）；社群層 `search_incident_knowledge`：**今晚全部 drill 只 1 次**（upstream-perm-denied，05:28） | 所以 drill 的秒數**幾乎不含社群層**；上面的 RAG 數字全部來自離線評測（`logs/kb-community-eval-*.json`、`logs/kb-judge*.json`、`logs/rerank-grid/`），不是從 drill 推的 |
| 社群層改為工具層推送後（A/B，ops tier 5 案 ×2，`docs/kb-community-20261022.md` Phase 4b） | 推送開 7/10、關 8/10；made-worse 0／謊報 0 兩臂皆然；10 場事故 4 次推送、1 次有參考、0 次被用上 | 第一版閘門被洪水與 log 歷史擋住（已修，commit `7d309ff`）；**RAG 對 drill 的幫助目前仍未證實**，全目錄 45 案重測中 |
| runbook 檢索延遲 | 首查 4.0 s（bge-small 載入，ops_api 啟動時預熱）；之後 80–90 ms | `scripts/rag.py`，3 句實測 |
| 修復卡 phrase gate | 漏推 0/8 → 6/8；誤推 3 → 0 | `scripts/kb-gate-probe.py` |
| 一次性診斷 benchmark（63 題、封閉標籤） | Lightning strict 6%，RAG 無幫助 | 這題不是 KB 的用途，誠實列出 |

## 5. 架構與安全（可對照的「有／沒有」）

| 項目 | 我們 |
|---|---|
| 離線 | 全部本地：模型、語料、sandbox；外網斷掉照跑（demo 有斷網段） |
| 變更路徑 | 單一：白名單工具 → runbook gate → NeMo Guardrails rail → reviewer 兩把鑰匙（code key＋模型）→ 風險動作人工審批 → 動作後自動健康檢查 |
| 診斷 | 唯讀工具 23 個＋平行證據包＋（選配）唯讀雙專家並行；破壞性工具永遠不給 |
| 監看 | 2 s 健康巡檢＋triage＋派工；選配訊號層（容器狀態、log 錯誤、磁碟、docker events）——實測有偵測但兩個安靜故障 240 s 內未結案，demo 不開 |
| 學習 | 結案自動寫「已驗證事故記憶」，下次先查；不重訓模型 |
| 資源治理 | 記憶體包絡決定 specialist 並行數、reranker、語料大小 |

## 6. 跟競品對照時怎麼問

1. 自主修復率怎麼定義？有沒有「越修越壞」與「謊報」的計數？（我們 0／0）
2. 盲測：故障寫完之後 agent 有沒有再改？幾題？（我們 18 題、17 過）
3. 偵測到派工幾秒？結案幾秒？拆解到模型／框架／工具各幾秒？
4. 模型在裝置上的 tok/s、冷啟動、整機記憶體？推測解碼有沒有驗證輸出沒壞？
5. RAG 的「對題率」是真相關還是類別命中？held-out 有沒有從索引排除？
6. 破壞性指令注入（log、設定檔）擋得住嗎？有沒有審核與人工審批？

## 7. 還沒量／要注意

- Thor 上的 DSpark／k=2／MTP 未驗（Spark 數字）；Thor round 1 基本配方 10/12，round 2（記憶推送）、round 3（reranker）、round 4（新四案）進行中。
- WATCH_SIGNALS 訊號層：偵測有、結案慢（兩案 >240 s），預設關。
- adaptive 多代理（Gate 2，7 案 ×2）：12/14 對單代理 7/7、中位 130／132 s 對 54／74 s、1 次謊報（upstream-wrong-content）→ demo 不開，旗標留著。
- 判官本身是模型，標籤是「LLM-judged」不是人工真值。
- 競品數字一律未量；表裡沒有任何估計值。
