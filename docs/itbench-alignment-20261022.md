# ITBench 指標對照（2026-10-22 13:10）— 我們怎麼拿公開 benchmark 的「尺」來量自己

**先說清楚**：ITBench（IBM Research，2025；SRE／CISO／FinOps 三組情境，SRE 組約 94 個 Kubernetes 事故情境）要跑起來需要一座 Kubernetes 叢集（kind／minikube）、
它自己的情境部署器（Helm chart、chaos 注入）、OpenTelemetry 觀測堆疊與 agent 介面（agent 透過 kubectl／API 操作叢集）。這些在 Jetson AGX Thor（arm64、離線、
今天 14:00 freeze、靶機被全目錄 regression 佔用）**今天跑不起來**；我們不會寫一個沒跑過的 ITBench 分數。
能誠實做的是兩件事：(1) 用 **ITBench 的指標定義**量我們自己的情境；(2) 已經在**公開 AIOps benchmark 的 held-out 題**上量過一次性診斷（結果不好看，照列）。

## 1. 同一把尺：ITBench 指標定義 → 我們的量法與數字

| ITBench 指標（定義） | 我們對應的量法 | 我們的數字（來源） |
|---|---|---|
| **Mitigation pass@1**（一次嘗試內把服務恢復到健康） | drill 自主修復：agent 自己把兩個健康 URL 修回 200 且內容正確，無兜底 | 全目錄 36 案 **30/32（94%）**、越修越壞 0、謊報 0（`logs/regression-20261022-045052`）；Thor 本機 12 案 10/12、修檔不換服務 3/3 |
| **Diagnosis / root-cause correctness** | 結案報告的 Root cause 行命中該案 `cause` 正則（`score.py` cause named） | 五張牌 100%、全目錄平均見 `docs/scorecard.md`（計分是正則命中，不是人工真值） |
| **MTTD（偵測時間）** | watchdog 從注入到開事故 | **4.0–4.9 s**（巡檢 2 s、連兩次異常） |
| **MTTR（恢復時間）** | 注入 → 兩 URL 200 | 簡單故障 12–21 s；全目錄中位 33 s（seen）；Thor 基本配方中位 107 s（seen）、141 s（今日全部） |
| **Efficiency：steps／tokens／cost** | 每事故工具呼叫數、模型產出 tokens、耗電 Wh | 中位 2–9 次呼叫（簡單故障）；decode 14 s／事故（Spark）；儀表板結案橫幅即時顯示本場 tokens 與 Wh |
| **Safety：unsafe actions** | 破壞性工具執行次數、注入指令被擋 | 破壞性 0 次；log／設定檔注入 2/2 擋下；規則擋下／審核否決計數在儀表板 |
| **Generalization（未見情境）** | 盲測 split：寫完故障不改 agent 的四批題 | 17/18 → 工具層補強後 18/18 |

## 2. 公開 benchmark 上真的跑過的：一次性診斷（不是我們的強項，照列）

`scripts/kb-eval.py`，63 題 held-out（RCAEval 27、Cloud-OpsBench 15、AIOps2025 17、RCA100 4），只給遙測、封閉標籤集、一次回答、無工具：
Nemotron-3-Nano strict 22–24%、Lightning strict 6%，加 RAG 無幫助（`docs/kb-eval.md`、`docs/kb-eval-lightning.md`）。
這題型（純遙測一次猜標籤）不是本系統的用途；我們的價值在工具迴圈裡逐步取證＋修復＋驗證，所以第 1 節才是對照的重點。

## 3. 賽後要真的跑 ITBench 的最短路徑

1. Spark（x86 無關，GB10 arm64 亦可）起 kind 叢集，裝 ITBench 的 SRE 情境 chart 與觀測堆疊。
2. 寫一個 `k8s_*` 工具組（kubectl 唯讀＋受審核的 rollout／restart），形狀同今天的 `vm_*`：白名單、runbook gate、動作後驗證、兩把鑰匙。
3. 先跑 SRE 組裡與我們相近的 10 個情境（Pod crash、config 錯、資源上限、網路策略），用 ITBench 自帶的評分器出 pass@1／MTTR。
4. 再決定要不要投全部 94 個。
