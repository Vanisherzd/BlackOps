# Blackout Ops 專案進度（2026-10-20 晚）

離 10/21 工作坊 1 天、10/22–23 黑客松 2 天、10/23 12:30 code freeze。
資料來源：git log（kb-retrieval-v2 共 50 個 commit，比 main 多 23 個）、`docs/`、`logs/`、決賽 Demo 執行手冊（10/14）。

## 1. 一句話現況

診斷修復的核心（agent、guards、雙鑰匙審核、知識庫、計分）在 DGX Spark 上**已經成熟**，並且經過盲測量化。
**demo 外殼**（遠端 victim、dashboard、watchdog、斷網、審批、Tier、功耗）今天才補上骨架，**還沒接到既有程式，也還沒在 Thor 上跑過**。

## 2. 時程對照

| 時間 | 計畫 | 狀態 |
|---|---|---|
| 9/27 | agent 迴圈、runbook 閘門、scorecard、雙鑰匙 change reviewer | ✅ 9/9 seen cases autonomous |
| 9/29 | 12 頁企劃書（Beamer v1→v2） | ✅ 已交 |
| 9/30 | 知識庫遷入 repo、`search_incident_knowledge`、KB eval（壓縮曲線） | ✅ |
| 10/13–14 | retrieval v3、第一次盲測（5 案） | ✅ |
| 10/17–19 | 盲測 batch 2–4、KB v4 repair cards、expert pruning、guards 放寬 | ✅ |
| 10/19–20 | thinking-off A/B、shim 量測健康並駁回錯誤的「healthy」 | ✅；**A/B 仍在 DGX 上跑**（`logs/ab-20261020-155259*`） |
| 10/20 前（手冊） | #1–#8 基礎設施，加上 victim 筆電、離線包、向主辦確認 | ⚠️ 見 §4；**落後約 3–4 人天** |
| 10/21 | Thor 實機：機型、nvpmodel、MIG、推論引擎、NemoClaw onboard、tegrastats rail 名稱 | ⬜ |
| 10/22 | 部署到 Thor、#10–#12、傍晚檢查點（第二幕連續 3 次成功）、夜跑 | ⬜ |
| 10/23 | 收數據、排練 3 次、12:30 freeze | ⬜ |

## 3. 已完成而且有數據的部分（可直接上簡報）

**Agent 能力**（Nemotron-3-Nano-30B-A3B-NVFP4，TensorRT-LLM，Spark）
- seen 9 案：29/30 autonomous（`docs/scorecard.md`；最早期歷史 28/34）
- 盲測：
  - batch 1：6/15
  - batch 2：11/15，根因說對 8/15
  - batch 3：v3 4/12、v4 5/12
  - batch 4：v3 6/12、v4 3/12
- 盲測的主要限制：需要的資訊沒有工具拿得到（記憶體上限、known-good 設定），或 guards 的條件是為已知故障寫的。
- 對照模型 Bolt（branch `feat/blackbox-bolt-vllm`，worktree `~/edge-agent-bolt`）：seen 20/20、blind 8/15；Nano 為 29/30、6/15。
- thinking off：4–5× 快，但 6/18 vs 11/18，且有 6 次錯誤的健康宣稱。shim 加上「系統自己量測健康」後：錯誤宣稱 6→0，resolved 6→9/18。
- expert pruning（128→112/層）：21/25，與未剪枝相同；18.5→16.1 GiB。

**安全機制**
- runbook 閘門：沒讀過 runbook 就拒絕動作。
- 雙鑰匙：程式檢查前置條件，加上 reviewer agent 判斷。
- 每次拒絕都寫明是誰拒絕、下一步該呼叫什麼。
- cleanup 只刪 allowlist 裡的檔案，證據 log 不可刪。
- 已記錄的 made-worse 案例：batch 4 有 2 次（誤修了健康的前端設定）。

**知識庫**（詳見 `docs/kb-coverage-survey.md`）
- 1,612 筆，其中可執行的卡 31 張、演練經驗 17 筆。
- 公開 held-out 上 RAG 的效果在雜訊內。
- 對真實機房錯誤句的覆蓋率 20%（14/70）。硬體、電源、Proxmox、K8s、L2/L3 網路幾乎是零。
- 壓縮曲線已有：none 的工作點 16×、rag 的工作點 32×，準確率持平，輸入 token 降 47–56%（`docs/kb-eval-v3-unseen.md`）。

## 4. Demo 外殼（決賽手冊的 14 項）

10/20 新增於 branch `demo-infra`。全部是新檔，預設 inert 或 opt-in，沒有改動既有檔。

| # | 項目 | 狀態 | 剩下要做的 |
|---|---|---|---|
| 1 | 遠端 victim | ❌ | 改 `faults.py`、`agent.py`（1–1.5 天，另開 branch/worktree） |
| 2 | prompt 去掉 8880 | ✅ branch | `upstream-502.md`、`AGENTS.md` 已改；8880 只有遠端 victim 才需要動 |
| 3 | 看板頁 | ✅ branch | 掛載與 write_config 規則已加；需要 `faults.py setup` 重建容器並跑回歸 |
| 4 | watchdog | ✅ 骨架 | 實際叫醒 agent 的路徑還沒測 |
| 5 | 歸檔 | ✅ `run-archive.sh` | 實跑一場（要等實驗結束） |
| 6 | wan.sh | ✅ 骨架 | 在有 sudo 的機器上實測 down/up |
| 7 | dashboard | ✅ 骨架 | 用瀏覽器看版面；從台上筆電經會場網路連線 |
| 8 | preflight | ✅ | — |
| 9 | Thor serve | ⚠️ branch 已準備 | Jetson 自動用 `--runtime nvidia`；`trt/thor-*.yaml`、`serve-tier1.sh` 已備；剩 4B 模型 ID、parser 相容性、記憶體比例，**只能在 Thor 上驗證** |
| 10 | 人工審批 | ✅ branch 已接線 | 回歸測試 |
| 11 | Tier 控制器 | ✅ branch 已接線 | 驗證 4B/30B 的 parser 相容性（需要 Tier-1 server） |
| 12 | power | ✅ | Thor 的 rail 名稱 |
| 13 | overnight.sh | ✅ branch | 實跑 |
| 14 | 能耗計分 | ✅ `score-runs.py` | score.py 不動；以合成資料驗證 |

既有檔的精確修改見 `docs/demo-pending-edits.md`。**實驗結束後**，由檔案的負責人套用，再跑回歸。

**今天就能跑的退路 demo**：`faults.py setup`，然後 `scripts/nemoclaw-drill.sh <case>`，在終端看 log（Spark 上可用）。

## 5. 主要風險（依嚴重度）

1. **硬體平台還沒在實機驗證。** 企劃書與手冊寫的是 Jetson Thor，但 `docs/hackathon-runbook.md` 和全部實驗都在 DGX Spark 上。trtllm-serve 在 Tegra 上未驗證，這台也沒有 4B 模型。10/21 是唯一的驗證窗口。
2. **企劃書的主張還沒有數據。** 「信心分級調度＋nvpmodel」的能耗（表一、表二）一筆都沒有，MIG 也還不確定。10/22 傍晚的檢查點如果沒過，要改用口頭和夜跑數據交代。
3. **demo 外殼落後。** 接線和遠端 victim 都會改到核心檔，跟仍在進行的實驗衝突，需要協調誰在什麼時候改。
4. **知識庫廣度。** 評審若問硬體或電源類故障，目前答不出來。補卡建議見 `docs/kb-coverage-survey.md` §4；比賽前只建議做 D 組與少量 A 組。
5. **共用主機。** 多人共用同一個工作目錄，experiment 會從工作目錄熱載入程式。建議每人用 `git worktree`，不要在共用目錄切 branch。

## 6. 建議的下一步（依序）

1. 等 A/B 實驗結束，寫結論並 commit（負責人：vanli）。
2. 套用 `docs/demo-pending-edits.md` 的 §1（prompt）與 §2（審批）。風險低，套完跑 9 案回歸。
3. 在 Spark 上實跑一次 `run-archive.sh upstream-500`、`watchdog.py`、`dashboard.py`，用瀏覽器看畫面。
4. 10/21 Thor：照手冊工作坊的清單做，並優先確認 #9。
5. 10/22：§3 的 Tier 接線、遠端 victim（如果要做）、`overnight.sh`。
6. 器材清單、離線包、向主辦確認的事項（電源、sudo、switch、有線網路）：repo 裡看不到進度，**請負責人自行確認**。

## 7. 檔案索引

| 主題 | 檔案 |
|---|---|
| 架構與每項設計決策 | `docs/agent-design.md` |
| 計分卡 | `docs/scorecard.md`、`scripts/score.py` |
| KB 評估 | `docs/kb-eval*.md`、`docs/kb-retrieval-v2.md`、`docs/kb-repair-cards.md` |
| KB 覆蓋度與 survey | `docs/kb-coverage-survey.md`、`scripts/kb-probe.py` |
| demo 待辦 diff | `docs/demo-pending-edits.md` |
| 企劃書 | `docs/proposal-12-pages.md`、`docs/proposal/` |
| Spark 部署 | `docs/hackathon-runbook.md`、`scripts/bootstrap-spark.sh` |
| 效能 | `docs/bench-2026-09-22.md`、`docs/serving-metrics-2026-09-22.md`、`docs/expert-pruning.md` |
