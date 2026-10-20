# Blackout Ops 專案進度（2026-10-20 23:40 更新）

時程：10/21 工作坊（剩 1 天）→ 10/22–23 黑客松 → 10/23 12:30 code freeze。
資料來源：三條分支的 git log、`docs/HANDOFF-kb-retrieval-v2.md`（vanli）、`logs/`、今晚的回歸測試。

## 1. 一句話現況

- **診斷修復核心**已經成熟並有量化數據：agent、安全規則、三把鑰匙、知識庫、計分卡，都在 kb-retrieval-v2 上。
- **demo 外殼**：決賽手冊 14 項中 12 項已在 `demo-infra` 上完成並接線，host 迴圈回歸無退步。
- **還沒驗證的兩件事**：NemoClaw 路徑上的 demo 外殼，以及**整套系統在 Thor 上跑**。

## 2. 分支

```
main (aec7c0c, 9/30)
 ├─ kb-retrieval-v2 (vanli, 48b16f9, 10/20 23:08)   主線：agent、guards、KB、shim 加速
 │    └─ demo-infra (我們, 已 merge 48b16f9)          demo 外殼；PR 目標：kb-retrieval-v2 → 最後到 main
 └─ feat/blackbox-bolt-vllm (ca74d6a, 10/17)          獨立的 blackbox/ 系統＋Bolt 74B on vLLM :8200
```

- repo 已改名為 **https://github.com/Vanisherzd/BlackOps**，舊網址會自動轉過去。
- demo-infra 合進 kb-retrieval-v2 **沒有程式碼衝突**，並已 merge 最新的 48b16f9。
- bolt 分支從 9/30 的 main 分出，沒有 kb-retrieval-v2 和 demo-infra 的任何東西。demo 外殼在 bolt 分支上**無效**，因為它不走 `scripts/agent.py`、`ops_api`、`shim`。合併時只有 `.gitignore` 會有文字衝突。
- Bolt 模型可以當 Tier-2 用：以 `edge-agent` 的名稱開在 :8200，設 `UPSTREAM_T2=http://127.0.0.1:8200`。要注意記憶體，這台沒有 swap。
- `~/edge-agent`（kb-retrieval-v2）已清乾淨，我們的檔案都在 `~/edge-agent-demo`（demo-infra）。

## 3. 已完成而且有數據的部分（可直接上簡報）

### Agent 能力
環境：Nemotron-3-Nano-30B-A3B-NVFP4、TensorRT-LLM、DGX Spark。

- 見過的 9 案：29/30 autonomous（`docs/scorecard.md`）。
- 盲測：
  - batch 1：6/15
  - batch 2：11/15
  - batch 3：v3 4/12、v4 5/12
  - batch 4：v3 6/12、v4 3/12
  - 主要限制：需要的資訊沒有工具拿得到，或安全規則只為已知的故障寫好。
- **分情況開關思考＋開場資料**（vanli，`fd7a79a`，各 54 場）：修好 43/54 vs 41/54，模型呼叫少 45%，見過的題目中位時間 56→38 秒，0 謊報、0 越修越壞。
- shim 的健康保護網（`189de86`）：關閉思考時，謊報從 6 降到 0。
- 對照 Bolt：見過的題目 20/20、盲測 8/15；Nano 是 29/30、6/15。
- expert 剪枝 128→112：21/25，和原版相同，18.5→16.1 GiB，但推論不會變快。

### 知識庫
詳見 `docs/kb-coverage-survey.md`。

- v4 沒有可量測的效果，**預設維持 v3**。
- 對真實機房錯誤句的覆蓋率 20%（14/70）：硬體、電源、Proxmox、K8s、L2/L3 網路幾乎是零。
- 壓縮曲線：準確率持平，輸入 token 降 47–56%。

## 4. Demo 外殼（決賽手冊 14 項；細節見 `docs/demo.md` §6）

| 狀態 | 項目 |
|---|---|
| ✅ 完成且接線 | #2 prompt 修正、#3 廠務看板、#7 dashboard、#8 preflight、#10 人工審批、#11 Tier、#12 功耗、#14 能耗計分（`score-runs.py`） |
| ✅ 骨架完成，未實跑 | #4 watchdog（叫醒 NemoClaw 未測）、#5 run-archive、#6 wan.sh（需要 sudo）、#13 overnight |
| ⚠️ 已準備，要在 Thor 驗證 | #9 Thor 推論：runtime nvidia、`thor-128g`／`64g`、`tier1.yaml`、`serve-tier1.sh` |
| ⏸ 延後 | #1 遠端 victim：10/22 傍晚檢查點後再決定 |

**今晚另外做的**：
- 廠務包裝：示範晶圓廠的看板頁，dashboard 改用「產線看板前端／MES API」的名稱。
- 看板頁改由 dashboard 提供：前端掛掉時頁面會轉紅，不會變成白頁。
- 修正 vanli 交接文件 §4.1 的 bug：被拒絕的那一輪不再關掉思考，`approval-rejected:` 也一併排除（`f34a08e`，**未經 drill 量測**）。
- `docs/demo.md` 依實際狀況修訂（原版保留在 git 歷史 `087b503`）。

**回歸測試**（host 迴圈，共用 victim，22:56–23:18）：
- 見過的 9 案中 6 案一次就過。另外 3 案重跑 1 次，branch 版過 2 案、原版過 1 案，差距在隨機波動範圍內；失敗的紀錄都沒有碰到新加的程式。
- 打開審批和 Tier 跑 upstream-500：PASS，Tier 1→2→1 都有紀錄。
- 跑完後，victim 已用 `~/edge-agent` 的程式重建回原狀。
- 細節見 `docs/demo-pending-edits.md`。

## 5. 主要風險（依嚴重度）

1. **硬體平台還沒驗證。** 到目前為止全部在 DGX Spark 上做；trtllm-serve 在 Tegra 上沒跑過；Tier-1 的 4B 模型 ID、parser 相容性、記憶體比例都還不知道。10/21 是唯一的驗證窗口。
2. **企劃書主張的能耗還沒有數據。** 表一、表二仍然空白。要等 Thor 上有 Tier-1 server 和 nvpmodel，才能跑 `overnight.sh`。MIG 也還不確定。
3. **NemoClaw 路徑的 demo 外殼還沒測。** drill 只在 `~/edge-agent` 裡跑，要等 demo-infra 合進去，或直接在 Thor 上一次 clone。
4. **既有成果揭露。** 審批和能耗治理原本計畫在黑客松期間完成，現在已提前做完，揭露頁要照實寫。
5. **共用的 Spark。** victim、沙盒、shim、推論服務都只有一套，跑 drill 前要先跟 vanli 協調時段（HANDOFF §0）。
6. **知識庫廣度。** 評審若問硬體或電源類故障，目前答不出來。

## 6. 接下來（依序）

**今晚／10/21 早上**
1. push demo-infra，開 PR 到 `kb-retrieval-v2`，請 vanli review；之後一路合到 main。
2. 跟 vanli 協調一個時段，在 `~/edge-agent` 用合併後的版本跑 NemoClaw 路徑：
   - 5 張卡 × 3 次。
   - watchdog 叫醒 agent 實測一場。
   - `run-archive.sh` 實跑一場。
   - 打開 `SHIM_ADAPTIVE` 重跑 B 組，驗證 §4.1 的修正。
3. 準備離線包：Thor 推論映像、4B 模型、HF cache、wheels。

**10/21 工作坊**
4. 照 `docs/demo.md` §7 的清單做，**第一優先是 #9 Thor 推論能跑起來**。

**10/22**
5. 部署到 Thor → 9 案回歸 → 傍晚檢查點（第二幕連續 3 次成功）→ 決定遠端 victim 要不要做 → 夜跑。

**簡報**
6. 加上廠務包裝；措辭改成「規則巡檢不推論」；加上同機部署的 Q&A 答案；更新揭露頁。

**器材與主辦**
7. 電源供應器、sudo、switch、有線網路：**repo 裡看不到進度，請負責人確認**。

## 7. 檔案索引

| 主題 | 檔案 |
|---|---|
| Demo 執行手冊（修訂版） | `docs/demo.md` |
| demo 修改紀錄與回歸 | `docs/demo-pending-edits.md` |
| kb-retrieval-v2 交接文件（vanli） | `docs/HANDOFF-kb-retrieval-v2.md` |
| 架構與每項設計決策 | `docs/agent-design.md` |
| 計分卡 | `docs/scorecard.md`、`scripts/score.py`、`scripts/score-runs.py` |
| KB 評估、覆蓋度與 survey | `docs/kb-eval*.md`、`docs/kb-repair-cards.md`、`docs/kb-coverage-survey.md`、`scripts/kb-probe.py` |
| 企劃書 | `docs/proposal-12-pages.md`、`docs/proposal/` |
| Spark 部署 | `docs/hackathon-runbook.md`、`scripts/bootstrap-spark.sh` |
| 效能 | `docs/bench-2026-09-22.md`、`docs/serving-metrics-2026-09-22.md`、`docs/expert-pruning.md` |
