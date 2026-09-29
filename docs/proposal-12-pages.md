# Blackout Ops — 企劃書 12 頁定稿內容（v1，2026-09-29）

> **2026-09-29 更新**：實際排版以 `docs/proposal/blackout-ops-deck.tex` 為準（Beamer 16:9，已編譯成 `docs/proposal/黃老大帶我飛_Blackout Ops｜機櫃裡的自主維運代理_EdgeAI企劃書.pdf`）。依組長指示改為 **CFP 對照 · 成就導向 · 貢獻明示**，刪除團隊分工頁：
> P1 封面＋成就數字條＋三項貢獻 · P2 場景與應用價值 · P3 CFP 對照表（四項能力＋四個評分項＋淘汰條款自檢）· P4 能力一 任務分解 · P5 能力二 工具調用（含工具面前後對照矩陣）· P6 能力三 狀態與動態調整 · P7 能力四 權限與風險（雙鑰匙審核）· P8 代理架構＋NemoClaw 整合 · P9 邊緣優化 I：Benchmark 比對（引擎對照、7 組選型矩陣、三種檢索 × 三個題集、權重掃描）· P10 邊緣優化 II：量測計畫矩陣（初賽給方法、對照組、通過標準；決賽於 Thor 交數字）· P11 評估矩陣（九案逐案計分）＋現場 demo · P12 貢獻、既有成果揭露、風險。
> 下方是原始逐頁文案，數字仍有效，頁序以上面為準。

> 給做簡報的人：每頁 = 1 個大標（≤16 字）+ 1 句 punchline + ≤4 個要點 + 1 個視覺。
> 「已量」= 在 lab DGX Spark 上量到的真數字，簡報上一律標 *measured on DGX Spark*；「規劃」= 決賽期間在 Jetson AGX Thor 上實作，簡報上一律標 *決賽期間實作*。
> 全篇禁令：不放任何外部連結；不用「低功耗裝置」當絕對形容詞；不以記憶體縮減或模型縮小當主張；不出現 engine 跨平台部署；不出現 DGX Spark 當離線優化節點；Qwen 不當主角；只用一個名字 **Blackout Ops**。
> 檔名：`黃老大帶我飛_Blackout Ops｜機櫃裡的自主維運代理_EdgeAI企劃書.pdf`（16:9、≤12 頁、≤20 MB，10/02 11:59 前）。

---

## P1 ｜ 封面（Hero）

**大標**：Blackout Ops
**副標**：當基礎設施崩潰時，仍然活著的自主維運代理
**英文一行**：An out-of-band, fully local, self-healing SRE agent for air-gapped and degraded networks
**三個 badge**：Fully Local ・ Self-Healing ・ Policy-Constrained
**部署**：Jetson AGX Thor（NemoClaw / OpenShell ・ Nemotron-3-Nano ・ TensorRT-LLM）
**隊伍**：黃老大帶我飛
**視覺**：機櫃裡一台小裝置，往雲端的箭頭被打叉；裝置內四個字：Observe / Reason / Repair / Verify
**頁底一句**：Operational intelligence belongs next to the infrastructure it protects.

---

## P2 ｜ 監控者不能與被監控者共命運（Hero，Application Value）

**Punchline**：今天的問題不是 AI 不夠聰明，而是維運系統會跟著故障一起消失。
**要點**
- 現行 AIOps 跑在它要保護的那座基礎設施上：叢集耗盡、儲存故障、WAN 中斷，監控一起死
- 硬體早有答案：BMC / IPMI 獨立供電、獨立網路、獨立於主系統
- Blackout Ops 把帶外管理從「開關機與感測」提升到「推理與決策」
- **成本重定義**：一台常時開機、與機櫃同壽命的獨立裝置，真實成本不是峰值算力，是累積能耗（P8 的伏筆）
**視覺**：左「Traditional：Workload / Monitoring / AIOps 同一個 failure domain → ✕」，右「Blackout Ops：獨立的 Edge Agent → ✓」

---

## P3 ｜ 有一個市場不是斷線，是本來就不准連（Architecture，Application Value）

**Punchline**：隱私、即時、無網——這一頁一次吃滿三個關鍵字。
**三層情境表**

| 層級 | 情境 | 雲端 AI | Blackout Ops |
|---|---|---|---|
| L1 最常見 | WAN／DNS／防火牆／憑證 | ✕ | ✓ 全功能 |
| L2 法遵 | 金融核心、國防、半導體廠務的政策性氣隙 | ✕ 本來就禁止 | ✓ 全功能 |
| L3 降級 | 部分網段失聯 | ✕ | ⚠ 標示不可達區域，其餘照常 |

**要點**
- L2 不是「斷線」而是「不准連」：沒有替代方案的市場
- 診斷資料本身就是攻擊面地圖：日誌、設定、拓撲、憑證路徑不能離開現場
- 本地推論在此不是效能選擇，是資安要求
**視覺**：三層情境表本身

---

## P4 ｜ 所有元件自帶：被監控叢集全滅，診斷能力不減（Architecture）

**Punchline**：Model reasons. RAG remembers. Tools observe reality. Policy controls impact.
**架構圖（用真實架構畫）**
```
警報 / 監看 ──► NemoClaw sandbox（OpenShell：policy、default-deny、審計）
                   │ inference.local
                   ▼
              相容層 shim ──► TensorRT-LLM：Nemotron-3-Nano-30B-A3B NVFP4
                   │
                   ▼
              指揮代理 ──► ops API（20 個白名單工具，HTTP + MCP）──► 被監控系統
                   │              │
                   │              └─► 審核代理（破壞性動作的第二把鑰匙）
                   └─► Runbook RAG（本地向量索引，CPU 上跑）
```
**要點**
- 推論、索引、知識庫、狀態全部在裝置內；`HF_HUB_OFFLINE` 強制，沒有任何對外呼叫
- 工具只走白名單 API，agent 的 shell 裡沒有 docker、沒有 systemctl
- 決賽在 Thor 上：同一套堆疊，JetPack 7 + NemoClaw on Jetson 官方路徑（*決賽期間實作*）
**角標**：已量 / 已建（DGX Spark）

---

## P5 ｜ 每一步都由上一步的證據決定（Architecture，Agent Architecture 25%）

**Punchline**：不是把工具全跑一遍，是 Observe → Hypothesis → Best next action → Verify。
**真實 transcript（原樣放，這是全篇最有說服力的一格）**
```
check_health   → front unreachable, api unreachable
docker_ps      → edge-victim Exited(1)         ← 附上 runbook：nginx-config-error
docker_logs    → [emerg] invalid number of arguments in "return" directive (x6)
read_config    → 看到缺分號的那行
write_config   → written, not applied until restart
docker_restart → health now: front 200, api 200 → 事故結束，回報
```
**要點**
- 證據工具會把對應 runbook 一起推進上下文；改變狀態的工具在沒看過 runbook 前一律拒絕（機制，不是提示詞）
- 動作結果自帶健康檢查：修好就停，沒修好就說哪個容器還壞
- 同一故障換路徑的真實案例：前端重啟被前置條件擋下（上游不在網路上），改走 `network_repair`
**數字**（已量，DGX Spark）：9 種注入故障，最新一輪 **9/9 自主修復**，中位數 **48 秒**，證據先行 100%，收尾驗證 100%
**角標**：已量

---

## P6 ｜ 破壞性操作永遠需要第二把鑰匙（Architecture，權限與風險控制）

**Punchline**：Self-Healing ≠ Root Shell。
**工具分級**

| 級別 | 內容 | 執行條件 |
|---|---|---|
| R 唯讀 | health、ps、logs、config、disk、journal、port、process | 自主 |
| W 可逆 | start、restart、network repair、寫回設定 | 自主 + 事後審計 |
| D 破壞性 | 重建容器、殺行程、清理磁碟 | **雙鑰匙審核** |

**雙鑰匙審核代理（已建、已測）**
- 鑰匙一（程式）：從事故軌跡算充分條件——重建前必須重啟過且仍失敗、殺行程前 `top_processes` 要列到、清磁碟前用量要超過 85%；缺一個直接拒絕並點名
- 鑰匙二（第二個代理，獨立 prompt、不給工具）：只判「與本次症狀相關嗎、有沒有一行證據反駁」，要拒絕必須引用那行；連不上就不執行（fail-closed）
- 實測：無證據殺行程 **REJECT**；量到 100% CPU 的失控行程 **APPROVE**；重啟後仍 500 的重建連續 **3/3 APPROVE**
**刻意不放進白名單的東西**：docker daemon（推論容器會跟著死）、任何系統服務、共用 image 與 volume——每一項都有理由，理由本身就是風險控制
**視覺**：LLM decides intent → Policy decides permission → Reviewer decides risk → Tool executes → Verification decides success
**角標**：已量

---

## P7 ｜ 本案是 NemoClaw 的典型用例，不是它的競品（Architecture，決賽要件）

**Punchline**：常時運行、對生產環境有權限、會執行指令的自主代理——這正是 NemoClaw 被設計來解決的最高風險情境。
**對照表**

| 本案需求 | NemoClaw 元件 |
|---|---|
| 指令必須隔離、不得逃逸 | OpenShell 沙箱 |
| 診斷資料絕不外流 | Network Policy default-deny（我們只開放推論與 ops API 兩個端點） |
| 工具以受控方式進沙箱 | MCP（20 個工具直接曝露，不經 tool-search 迷宮） |
| 完整操作審計 | OpenShell 審計 + 我們的 ops-api / reviews 紀錄 |
| 本地 Nemotron 推論 | TensorRT-LLM 後端（PyTorch backend 直跑 NVFP4 checkpoint，**不需 build engine，無跨平台問題**） |

**預備 Q&A「這不就是 NemoClaw？」**：NemoClaw 是執行基材；它不知道 `[emerg]` 之後該看哪個檔、不知道重啟前端會讓 nginx 解析不到上游而退出、不知道哪些動作在氣隙網段裡絕對禁止。這些領域決策全在我們的工具層、審核層與 runbook 裡。
**角標**：已建

---

## P8 ｜ 算力不是限制條件，是 Agent 自己要管理的資源（Evidence，Edge 35%）

**Punchline**：全場每隊都拿到 128 GB 的機器，「塞進受限硬體」對所有人同時失效；帶外裝置的真實成本是累積能耗。
**為什麼是 Thor**
- 配發清單裡就有 2U、9–48 V 寬壓、工業溫域的機型：主張「維運智能是機櫃裡的工業級獨立裝置」不用我們證明
- nvpmodel 可程式化功耗模式；MIG 把巡檢與推理分開——「Tier-2 推理不會餓死監控迴圈」從承諾變成架構保證
**機制圖（規劃，決賽期間實作）**
```
巡檢（低功耗模式 ・ Tier-1 ・ MIG 實例 A）
   │ 偵測異常 → 信心評估
   ├─ 信心足 → Tier-1 處理，不拉功耗
   └─ 信心不足 → 升 Tier-2 + nvpmodel 拉升 + MIG 實例 B
        └─ 事故結束 → 降回
```
**Tier 怎麼切（有數據的版本）**：Tier-1 = 同一顆 30B A3B **關 thinking**，Tier-2 = **開 thinking**。已量：一次事故的 token 從 1.5k 到 10k，幾乎全在 thinking——分級的第一刀切在這裡，而不是換更小的模型；4B 作為 Tier-0 候選，通過計分卡才升格
**MoE 論述**：30B 總參數、3B active，每個 token 實際搬動的權重才是邊緣推論的成本
**角標**：規劃 + 已量（token 分布）

---

## P9 ｜ 同一台機器、同一組故障，唯一差異是調度機制（Evidence，Edge 35%）

**Punchline**：控制變因乾淨，評審無法質疑在比兩台機器。
**主圖**：tegrastats 功耗曲線——巡檢平坦，故障注入出現尖峰，修復後回落（*決賽期間量測*，版面留最大）
**表一：能耗**（規劃，Thor）：平均功耗（巡檢／推理）、功耗動態範圍倍率、單次診斷能耗 Wh、24 h 累積能耗 Wh、低功耗模式時間佔比
**表二：調度效益**（規劃，自比）：全程 Tier-2 高功耗 vs 信心分級 + nvpmodel 連動——延遲、平均功耗、每瓦診斷次數、正確率不得下降、Tier-1 承接比例
**這頁現在就能放的真數字（已量，DGX Spark，標平台）**
- 推論：Nemotron-3-Nano-30B-A3B NVFP4 on TensorRT-LLM——單流 57.9 tok/s、8 併發 237 tok/s、12.9k token prefill 1.17 s、佔用 34 GB、啟動 106 s
- 品質：GSM8K 96.7%、IFEval 80%（lm-eval，本地端點）
- 每次事故 token：中位數約 2.7k，最少 1.5k，最多 10k（探索型案例）
- 我們也敢關掉沒用的優化：PDL 無感、NGram 投機解碼會炸、block reuse 讓首個長 prompt 從 1.2 s 變 18.7 s——全部不採用
**角標**：規劃（能耗）+ 已量（推論）

---

## P10 ｜ 不塞整包日誌，而且有曲線證明不塞更好（Evidence，Edge 35% / Agent Arch）

**Punchline**：128 GB 會誘使所有人把整包日誌塞進模型；我們主張不塞，並用數據證明。
**已量的輸入端治理（DGX Spark）**
- System prompt **28,859 → 14,200 字元**（−51%）：關掉 tool-search 迷宮、16 個無關 skill 砍到 1 個、通用助理指令換成一頁維運程序
- 日誌預處理已上線：stderr 依序合併、重複行折疊成 `(x6)`、截斷保留尾端——crash loop 的 `[emerg]` 從被截掉到成為最醒目的一行，該案例從 0/5 變 4/4
- 檢索：11 篇 runbook、33 題症狀查詢，向量 **28/33**，混合 23/33，關鍵字 19/33（我們原以為混合會贏，量了才知道）
**規劃（決賽期間）**：壓縮比 vs 診斷正確率曲線——規則過濾、去重、時窗聚合、關鍵字錨定四級，標示拐點與工作點
**四層動態調整**：計畫層（修復失敗換假設）、模型層（信心不足升 Tier）、硬體層（nvpmodel 升降）、通道層（審批通道隨網路降級）
**角標**：已量（prompt、log、檢索）+ 規劃（曲線）

---

## P11 ｜ 現場斷網：同一套流程，零降級（Evidence，Application Value）

**Punchline**：不是還能跑，是系統知道環境變了、自己換了通道。
**Demo 劇本（約 6.5 分鐘）**：帶外論述 45 s → 正常儀表板與功耗曲線 → 當場注入故障 → 功耗尖峰 → 逐步診斷（假設、工具、證據、信心）→ 修復計畫與 blast radius → 破壞性動作觸發審核代理 → 執行、驗證、功耗回落 → **切斷對外網段，重跑一次**，審批改由本地 UI 完成 → 對照表 → NemoClaw 整合與市場
**我們怎麼評估自己（已建）**：不是看回答，是看系統有沒有恢復。計分卡把每場演練分成自主 / 靠後備 / 誠實升級 / 弄更糟 / 沉默失敗，另量動作效率、證據先行、收尾驗證、回報是否點名 root cause、假健康宣稱、無關與重複呼叫、閘門與審核擋下次數、秒數、token
**當前數字（已量，DGX Spark）**：9 案新鮮回歸 9/9 自主、弄更糟 0、假健康宣稱 0；當天累計 86 場，失敗全數追到工具層缺陷並修正，沒有一個是靠改提示詞
**產品化路徑**：配發清單裡的工業機型就是部署形態；氣隙市場沒有替代方案
**角標**：已量（評估）+ 規劃（斷網 demo 於 Thor）

---

## P12 ｜ 團隊、時程、風險、既有成果揭露（Hero 收尾）

**團隊分工**（4–8 人）：Agent 邏輯 1–2、平台與優化 1–2、安全與整合 1、基礎設施與故障注入 1、前端與 Demo 1（本地審批 UI 必做）、簡報與敘事 1
**時程**：10/02 送件 → 10/09 入選 → 10/09–10/20 在自有硬體驗證 Thor 路徑與 tiering → 10/21 工作坊：配發機上跑通 NemoClaw、談定可控斷網網段、決定 harness → 10/22–23 決賽
**既有成果揭露（規則要求，必寫）**
- 2026/09/21–09/29 於實驗室 DGX Spark 建置並量測：NemoClaw/OpenShell 沙箱 + TensorRT-LLM 服務、20 個白名單工具的 ops API（HTTP + MCP）、11 篇 runbook 與向量檢索、9 種故障注入與 harness、雙鑰匙審核代理、演練計分卡
- 決賽期間新增：Jetson Thor 部署、nvpmodel／MIG 能耗治理、信心分級 Tier 路由、日誌壓縮曲線、本地審批 UI、未演練過的故障案例（unseen 通過率）
**風險與備案（各一行）**：Thor 上 TensorRT-LLM 可用性 → NIM／Ollama 基準線可 demo；配發 OEM 機型核心模組 → 10/21 優先驗證並找導師；Privacy Router 無法程式切換 → Tier 路由做在自有相容層；功耗動態範圍不如預期 → 日誌壓縮為第二條腿；Deep Agents 未測通 → 維持 OpenClaw 為執行 harness，決策層在工具與審核層
**配發偏好**：NVIDIA Jetson AGX Thor 開發者套件（原廠 BSP 風險最低）> ASUS PE3000N > Aetina AIE-KT78
**頁尾三句**：OBSERVE LOCALLY. REASON LOCALLY. RECOVER LOCALLY.

---

## 附：評分對照（不進 12 頁，給撰稿人核對）

| 評分 | 權重 | 頁 |
|---|---|---|
| Edge Optimization | 35% | P8、P9、P10 |
| Agent Architecture | 25% | P4、P5、P6、P7 |
| Application Value | 30% | P2、P3、P11 |
| Readability | 10% | 每頁一主張、數字取代形容詞 |

| 門檻 | 頁 |
|---|---|
| 1 目標與任務分解（必備） | P5 |
| 2 工具與系統調用 | P4、P6、P7 |
| 3 狀態與動態調整 | P5、P10 |
| 4 權限與風險控制 | P6 |

## 附：3 天內可以再補的真數字（都在 DGX Spark 上，標平台即可）

1. 開／關 thinking 的 A/B：九案各跑一輪，秒數、token、通過率對照 → P8 的 Tier 切法有數據
2. 日誌保留行數 vs 通過率的初版曲線（tail 20 / 50 / 100 / 全部）→ P10 的曲線先有 Spark 版
3. `nvidia-smi` 功耗取樣一整場演練 → P9 至少有一張真的功耗曲線（標 Spark，Thor 上重量）
