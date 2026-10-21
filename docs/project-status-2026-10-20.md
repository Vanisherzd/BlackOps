# Blackout Ops 專案進度（2026-10-21 10:40 更新）

時程：**今天 10/21 工作坊** → 10/22–23 黑客松 → 10/23 12:30 code freeze。
比賽機器：**NVIDIA Jetson AGX Thor**（JetPack 7，sm_110）。開發與驗證都在 DGX Spark（GB10）上做。

## 1. 一句話現況

功能面都做完了，在 Spark 上也驗證過：
- 核心系統、vLLM + Bolt 推論、NeMo 整合、demo 外殼，已經合進 `main`（`14a8f3e`）。

剩下兩類工作：
- **把整套搬到 Thor 上，跑通推論**，這是最大的風險。
- **demo 外殼裡還沒真的跑過的那幾項**，要排進 Thor 上的排練。

## 2. 分支與 repo

- GitHub：`Vanisherzd/BlackOps`。
- `main` 是 `14a8f3e`，PR #1 和 #2 都已合併；內容是 kb-retrieval-v2、Bolt 分支、demo 外殼、NeMo 的整合。
- `~/edge-agent` 在 `main` 上，所有選配功能預設關閉。
- `thor-bringup` 分支（worktree `~/edge-agent-thor`）**還沒 push**，內容是 `serve-vllm.sh` 的 `VLLM_PROFILE`、`docs/thor-bringup.md`，以及這份進度檔。
- 往後的 commit 一律用 `penny11124 <92087067+penny11124@users.noreply.github.com>`。已經在 `main` 上的舊 commit 作者仍是 `113064531`，不改。
- 組員的分支（`kb-retrieval-v2`、`feat/blackbox-bolt-vllm`）都沒動過。

## 3. 已完成而且有數據的部分（Spark）

| 項目 | 結果 | 文件 |
|---|---|---|
| 推論：vLLM + Nemotron-3.5-Bolt-74B-A9B，以 `edge-agent` 開在 :8000 | NemoClaw 路徑 5 卡 × 3 = **15/15**；host 迴圈 9 案 × 3 = **26/27**；tool_eval 26/27 | `docs/integration-vllm.md` |
| 舞台卡中位時間（Bolt，NemoClaw） | 40、31、46、66、106 s（nginx-stopped、container-removed、upstream-paused、bad-config、upstream-500） | 同上 |
| `SHIM_ADAPTIVE` + `TRIAGE` 跑 upstream-500 | 5/5，但中位數 131 s，沒有變快，所以舞台上預設不開 | 同上 |
| **NeMo Guardrails**（工具層的輸出 rail 和動作 rail） | `test-guardrails.py` **23/23**；開著 rail 跑 NemoClaw 舞台卡 5/5、`log-injection` 1/1；可疑行約 0.3 s，正常行 1 ms | `docs/nemo-integration.md` |
| **NeMo Agent Toolkit** profiling，6 場事故 | 每場約 **1 Wh**，其中 **57% 在 LLM**；prompt 和 completion 的 token 比約 **40:1**；事故時間平均 85 s | 同上 |
| TRT + Nano 時期的歷史成績 | seen 29/30；四批盲測 6/15、11/15、4–5/12、3–6/12 | `docs/scorecard.md`、`docs/HANDOFF-kb-retrieval-v2.md` |
| 知識庫 | v3 是預設；對真實機房錯誤句的覆蓋率 20%（14/70） | `docs/kb-coverage-survey.md` |

照實說：
- Bolt 在沒有 Guardrails 的情況下，也沒照著注入的指令去做（3/3）。所以這層 rail 是多一道防護。
- agent 的最終報告還不會回報注入事件。

## 4. demo 外殼：決賽手冊 14 項

| 狀態 | 項目 |
|---|---|
| ✅ 做完並驗證過 | #2 prompt 修正、#3 廠務看板、#5 歸檔（`run-archive.sh`，實跑 12 場）、#8 preflight、#12 功耗（Spark 上用 nvidia-smi）、#14 能耗計分（`score-runs.py`） |
| ⚠️ 做完了但**還沒真的跑過**，要排進 Thor 排練 | #4 watchdog 實際叫醒 NemoClaw、#6 `wan.sh`（需要 sudo）、#7 dashboard 的舞台模式（按鈕、核准卡）、#10 人工審批走 NemoClaw 路徑、#11 Tier 在兩台推論服務之間切換（還沒有 Tier-1 模型）、#13 `overnight.sh` |
| ⚠️ 已準備，要在 Thor 上驗證 | #9 推論：`serve-vllm.sh` 的 `bolt`／`generic`，A 到 D 四種退路（`docs/thor-bringup.md` §3） |
| ⏸ 延後 | #1 遠端 victim，10/22 傍晚再決定 |

## 5. 主要風險（依嚴重度）

1. **Thor 上的推論。** Bolt 映像裡的 b12x 是為 GB10（sm121）編譯的，在 Thor（sm_110）上能不能跑還不知道；查不到任何「Bolt 能在 Thor 上跑」的來源。退路依序是：Bolt 加 NGC 的 Thor vLLM、Nano 30B 加 NGC、Nano 加 upstream vLLM。如果退到 Nano，簡報和計分卡都要改。
2. **能耗表（企劃書的表一、表二）還沒有 Thor 上的數據。** 要等 Thor、Tier-1 模型、nvpmodel 都到位，才能跑 `overnight.sh`。
3. **MIG 在 JetPack 7.2 以前不支援**（依 NVIDIA 論壇的說法）。企劃書寫的「架構保證」可能要改。
4. **還沒真的跑過的 6 項 demo 外殼**（§4）。10/22 每一項都至少要實跑一次。
5. **NemoClaw 在 Thor 上的安裝需要網路**，離線包沒有涵蓋這部分。
6. **既有成果揭露。** Bolt 分支、demo 外殼、NeMo 整合都是賽前做的；大會規定沒揭露的既有成果可能被取消資格。請確認簡報的揭露頁有寫到。
7. **共用的 Spark。** 推論服務一次只能開一個，因為沒有 swap；跑 drill 前要先協調。

## 6. 要做的事

**今天（10/21，工作坊）**
1. push `thor-bringup` 並開 PR 到 `main`，**負責人：penny11124**。
2. 離線包（`/media/hsnl/TRANSCEND/thor-bundle-20261021/`），10:22 開始在背景做。
3. **讓推論在 Thor 上跑起來**：照 `docs/thor-bringup.md` 走。
   - §1 開機檢查。
   - §3 推論依 A 到 D 嘗試，`smoke.py` 要過。
   - §5 裝 NemoClaw（`NEMOCLAW_SANDBOX_GPU=0`），跑一場 drill。
4. 選定 Tier-1 模型（Nano 4B 或 Lightning），用 `serve-tier1.sh` 開起來，確認 shim 解析正常。
5. 填好紀錄表（`thor-bringup.md` §8）：機型、JetPack 版本、nvpmodel 模式表、tegrastats 電源軌、sudo、sandbox 網段。
6. 向主辦確認：電源供應器、sudo、能不能自帶 switch、台上能不能有線連到會場網路。

**10/22（黑客松第一天）**
7. Thor 上跑 9 案回歸，加上 §4 那 6 項，每一項至少實跑一次。
8. **傍晚檢查點**：第二幕（upstream-500 加審批加 Tier）連續成功 3 次，才保留 Tier 和 nvpmodel。
9. 決定要不要做遠端 victim。
10. 晚上排練一次「冷開機後直接斷網」。睡前開始跑 `overnight.sh`，Tier 的 A/B 測試。

**10/23（第二天，12:30 code freeze）**
11. 用 `score-runs.py` 和 `nat_profile.py` 把數據填進簡報的表一、表二。
12. 排練 3 次。挑一場最乾淨的 Thor 實跑當回放素材，同步到簡報筆電和備援筆電。

**簡報要更新的地方**
- 推論寫成「vLLM + Bolt；TensorRT-LLM 是早期的基準」，表四改成兩邊的對照。
- NVIDIA 整合頁補上 NeMo Guardrails 和 NeMo Agent Toolkit。
- 邊緣優化頁加上「每場約 1 Wh，57% 在 LLM，prompt 與 completion 約 40:1，所以優化槓桿在 prefill 和 context」。
- MIG 的措辭，看 JetPack 版本決定。
- 廠務包裝；「監控者和被監控者在同一台」的 Q&A 答案；「規則巡檢不推論」這個措辭。
- 既有成果揭露頁。

**還等決定**
- 第一幕要不要「注入後不等，直接講投影片」。
- 舞台上要不要用 `log-injection` 這張卡，還是只在 Q&A 時用回放展示。

**選配**
- 遠端 victim。
- 讓 agent 在報告裡回報注入事件。
- 補知識庫的硬體、網路類卡片（`docs/kb-coverage-survey.md` §4）。
- 在 Thor 上把 NemoClaw 和 OpenShell 的映像也存一份離線版。

## 7. 文件索引

| 主題 | 檔案 |
|---|---|
| **Thor 上機流程** | `docs/thor-bringup.md` |
| Demo 執行手冊（舞台流程、啟動順序、失敗預案） | `docs/demo.md` |
| 三條分支整合、vLLM 取代 TRT、驗證結果 | `docs/integration-vllm.md` |
| NeMo Guardrails 與 Agent Toolkit | `docs/nemo-integration.md` |
| kb-retrieval-v2 交接文件（vanli） | `docs/HANDOFF-kb-retrieval-v2.md` |
| demo 外殼的修改紀錄與回歸 | `docs/demo-pending-edits.md` |
| 知識庫覆蓋度與機房故障 survey | `docs/kb-coverage-survey.md` |
| 架構與每項設計決策 | `docs/agent-design.md` |
| 計分卡（TRT 時期） | `docs/scorecard.md` |
| 企劃書 | `docs/proposal-12-pages.md`、`docs/proposal/` |
