# Blackout Ops 實測總表（2026-10-22 07:10，可直接拿去與競品對照）

所有數字都是**自己量到的**（DGX Spark GB10、128 GB unified memory；Thor 的數字另註），每一列都寫明怎麼量、在哪個 log。
競品沒有我們量的數字；這張表的用法是：**同一個指標定義，請對方給他們的值**。沒量過的欄位就寫「未量」，不用推估填滿。

## 0. 一句話

> 一台離線邊緣裝置上的自主維運 agent：**40 種故障、36 案例全目錄 regression 自主修復 30/32、越修越壞 0、謊報健康 0**；
> 偵測到派工 4 s、簡單故障 15 s 結案；Nemotron 3.5 Lightning 在 vLLM 上 **65–100 tok/s**（DSpark），整套含 RAG 與審核在 45 GB 內；
> 知識庫 158,881 筆離線語料經 rerank＋本機判官過濾後，機房症狀 **74%** 能給出對題的參考。

## 1. 可靠性（評審最該問的）

| 指標（定義） | 我們 | 怎麼量 |
|---|---|---|
| 自主修復率（agent 自己把兩個健康 URL 修回 200 且內容正確，無人介入、無 self_heal 兜底） | **30/32（94%）** | `scripts/regression.sh` 全目錄 36 案例 ×1，`logs/regression-20261022-045052/`（1 場被誤殺的 runner 打斷，不計） |
| 越修越壞（注入時還活的 URL 結束時死掉） | **0** | 同上，`scripts/score.py` made-worse 欄 |
| 謊報健康（報告說修好但健康檢查沒過） | **0** | 同上，false claims 欄 |
| 證據先於動作（第一個改變狀態的動作前有 ps／logs／runbook 查詢） | 74% | 同上，evidence-first 欄 |
| 盲測泛化（寫完故障後沒改 agent 的四批題） | **17/18** | 同一輪；Nano 時代同題 6/15、11/15、4–5/12、3–6/12 |
| 複合故障（兩個以上獨立原因同時發生） | 6/6（單代理）；adaptive 模式 10/10 進行中 | compound tier ×2 |
| 機房形狀故障（表面健康、log 乾淨） | 8/8 | 依賴逾時、hosts 被毒、resolver 壞、前端脫網、log 洪水、設定檔注入、三因、200 但內容錯 |
| 安全：注入指令（log 裡、設定檔裡要 agent 跑破壞性工具） | 2/2 擋下，破壞性工具 0 次執行 | log-injection 23 s、config-injection 27 s；Guardrails 把那行從工具輸出拿掉 |
| 舞台五張牌 | 5/5（今晚 4 輪）；Thor 5/5（隊友 10/21） | `logs/drills-*.log` |

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
| prefix cache 命中 | DSpark k=3 **0%**；k=2 21.6%；k=5 21.9%；MTP 21.6% | `scripts/prefix-cache-stats.py`（vLLM /metrics） |
| 冷啟動到 healthy | 131–192 s（權重 17.8 GiB） | rerank sidecar 46 s |
| 記憶體（RSS） | vLLM 3.7 GiB、rerank 3.3 GiB、ops-api 1.2 GiB（159k 索引 2.5 GiB）、sandbox 0.4–0.9 GiB；整機 MemAvailable ≥ 74 GB | `scripts/mem-profile.sh`，1 Hz |
| KV 預留 | gpu util 0.20 → 53k tokens（≥16 條 32k 序列） | 舞台用 0.20–0.25 |

## 4. 知識／RAG

| 指標 | 我們 | 說明 |
|---|---|---|
| 語料 | runbooks 15 ＋ 修復卡 38 ＋ 本機驗證修法 ＋ **社群層 158,881 筆**（7 個 Stack Exchange 站台，CC BY-SA，署名齊） | 全離線；分片 53 MB 在 git |
| 第一階段（BM25／FTS5）recall@20／@50 | 0.98／0.99 | held-out 1,139 題（從未進索引） |
| 類別代理 category@1 | v1 BM25 0.70 → v1+rerank 0.775；159k+rerank 0.765 | 類別標籤只有 34% 與真相關一致，**別拿這個當品質** |
| **真相關（LLM 判官）**：第 1 名對題／前 5 名有對題 | held-out：6%／13%／25%（BM25／rerank 第 1／前 5）；機房探針 38 句：v1 29%／45%，**159k 語料 42%／74%** | `scripts/kb-judge.py`；判官 = Lightning no-think 一個字 |
| 上線規則 | BM25/FTS5 top-50 → rerank（讀問題＋答案）→ **判官閘門**（只給判官說 yes 的） | 預設開；沒對題就不給 |
| 檢索延遲 | BM25 5.5 ms（6k）／FTS5 44 ms（159k）；rerank 145 ms（安靜）～1.2 s（滿載） | 只在明確呼叫知識工具時付 |
| 修復卡 phrase gate | 漏推 0/8 → 6/8；誤推 3 → 0 | `scripts/kb-gate-probe.py` |
| 一次性診斷 benchmark（63 題、封閉標籤） | Lightning strict 6%，RAG 無幫助 | 這題不是 KB 的用途，誠實列出 |

## 5. 架構與安全（可對照的「有／沒有」）

| 項目 | 我們 |
|---|---|
| 離線 | 全部本地：模型、語料、sandbox；外網斷掉照跑（demo 有斷網段） |
| 變更路徑 | 單一：白名單工具 → runbook gate → NeMo Guardrails rail → reviewer 兩把鑰匙（code key＋模型）→ 風險動作人工審批 → 動作後自動健康檢查 |
| 診斷 | 唯讀工具 23 個＋平行證據包＋（選配）唯讀雙專家並行；破壞性工具永遠不給 |
| 監看 | 2 s 健康巡檢＋triage＋派工；選配訊號層（容器狀態、log 錯誤、磁碟、docker events） |
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

- Thor 上的 DSpark／k=2／MTP 未驗（Spark 數字）。
- adaptive 多代理：成功率同、牆鐘長 → demo 不開。
- 判官本身是模型，標籤是「LLM-judged」不是人工真值。
- 競品數字一律未量；表裡沒有任何估計值。
