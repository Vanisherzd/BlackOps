# Blackout Ops 專案進度（2026-10-21 晚上更新：Thor 上機完成）

時程（**2026-10-21 大會更動**）：**今天 10/21 工作坊** → **10/22 16:00 截止**（原本是 10/23 12:30，少了將近一天）。
揭露規則（同日更動）：**預賽到決賽之間的實作不算賽前成果**。只有預賽企劃書（10/02）之前就有的東西要列進揭露頁。
比賽機器：**NVIDIA Jetson AGX Thor T5000**（122 GB，R38.4 = JetPack 7.1，sm_110，沒有 MIG）。開發與驗證在 DGX Spark（GB10）上做，10/21 起在 Thor 上實跑。

## 1. 一句話現況

**整套系統已經在 Thor 上跑起來，11:00 的檢查點已經提前達成。**
- 推論改用 **Nemotron-3.5-Lightning-30B-A3B-NVFP4**（vLLM nightly + Marlin MoE）。原本的 Bolt 在 Thor 上不能跑（見風險 1）。
- 5 張舞台卡 **5/5**；第二幕（upstream-500 + dashboard 人工核准）**連續 3/3**；完整舞台彩排（第一幕 → 斷網 → 第二幕 → 恢復）跑通。
- Tier 與 nvpmodel 連動**依 demo.md §7 的規則砍掉**：實測在 Thor 上拿不出節能數據（`docs/thor-tier-report-20261021.md`）。保留人工審批。
- 啟動只要一個指令：`scripts/stage-up.sh`（推論沒開會先開 Lightning，再依序開舞台服務，最後 preflight）。

## 2. 分支與 repo

- GitHub：`Vanisherzd/BlackOps`。`main` 上有 #1–#8（#7 是 wan.sh 修正，#8 是 DGX 故障知識）。
- **`feat/thor-stage-up`**：Thor 上機的全部修正與文件，已合併最新 `main`（含 #8），等 PR。Thor 的 `~/BlackOps` 目前就跑這個分支的內容。
- Thor：`~/BlackOps`（symlink `~/edge-agent`）。ssh 請用**有線**介面 `nv@192.168.1.108`（Wi-Fi 的 .184 會掉包）。
- 往後的 commit 一律用 `penny11124 <92087067+penny11124@users.noreply.github.com>`。
- 組員的分支都沒動過。

## 3. Thor 上的實測（2026-10-21）

| 項目 | 結果 | 文件 |
|---|---|---|
| 推論：Lightning 30B NVFP4，以 `edge-agent` 開在 :8000（預留 0.30） | 首次 437 s、之後約 100 s ready；權重 17.8 GiB；推論約 64 W、每 token 約 1.2 J | `docs/thor-tier-report-20261021.md` |
| 舞台卡（NemoClaw 路徑） | **5/5**，made-worse 0：25 / 27 / 340 / 26 / 95 s（nginx-stopped、container-removed、bad-config、upstream-paused、upstream-500）；合併 #8 後 bad-config 52 s | 同上 §5、§6 |
| 第二幕 × 3（dashboard 注入 → watchdog → agent → 核准卡 → dashboard 核准） | **3/3**：81.9、127.1、87.0 s | 同上 §6 |
| 完整舞台流程（第一幕 → 斷網 → 第二幕 → 恢復，照 demo.md §3.2 的間隔） | 修正後 2/2：第一幕 22.0 / 20.8 s，第二幕 152.1 / 108.1 s；結案後看板不再被改壞 | `docs/demo.md` §3.5 |
| NeMo Guardrails（`test-guardrails.py`） | 連續 3 次 **23/23**（修正 URL 編碼與「換個寫法再判一次」之後） | `docs/thor-bringup.md` §9 |
| 其他候選模型 | Nano 30B（vLLM 可、TRT-LLM 不可）、Super 120B（可跑但慢且吃光記憶體）、Nano-4B 當 Tier-1（比 Lightning 更耗能） | `docs/thor-tier-report-20261021.md` |
| Spark 上的歷史數據（Bolt） | NemoClaw 5 卡 × 3 = 15/15；Guardrails 23/23；NAT profiling 每場約 1 Wh | `docs/integration-vllm.md`、`docs/nemo-integration.md` |

## 4. demo 外殼：決賽手冊 14 項

| 狀態 | 項目 |
|---|---|
| ✅ 在 Thor 上實跑過 | #2 prompt、#3 廠務看板、#4 watchdog 叫醒 NemoClaw、#5 歸檔（watchdog 每場自動存 `logs/runs/`）、#6 `wan.sh` 斷網與恢復、#7 dashboard 按鈕與核准卡、#8 preflight（GREEN）、#9 推論、#10 人工審批走 NemoClaw 路徑、#12 功耗（tegrastats `VIN`） |
| ✂ 依 11:00 規則砍掉 | #11 Tier 切換（Tier-1 比 Tier-2 更耗能，見報告）、nvpmodel 連動（120W 模式實測功耗不變） |
| ⚠️ 沒在 Thor 上跑 | #13 `overnight.sh`（Tier 砍掉後不需要 A/B）、#14 能耗計分改用報告裡的 J/token |
| ⏸ 不做 | #1 遠端 victim |

## 5. 主要風險與處理（依嚴重度）

1. **Bolt 不能在 Thor 上跑（已處理）。** Bolt-24GB 的 dense 層是 block 量化，vLLM 規定只能用 b12x，而 b12x 只為 SM12x（GB10）編譯；隨身碟裡的映像也一樣。改用 Lightning。**簡報與計分卡要改**（§6）。
2. **agent 結案後還會繼續動作（已處理，要知道）。** OpenClaw 會延續已結束的 session；第一輪舞台彩排中，看板轉綠 30 秒後 agent 寫壞 `default.conf`，前端又掛了。現在三層防護：工具層在系統健康時拒絕一切改動；watchdog 在結案 60 秒後停掉 agent、派新 agent 前先停舊的；`stage-up.sh` 清掉殘留的 agent。
3. **FlashInfer 的 JIT 快取不能刪。** 備援的 Nano 30B 首次啟動要編 90 分鐘；Lightning 的小型 JIT 也存在 `~/.cache/flashinfer*`、`~/.cache/vllm-compile`。
4. **Thor 沒有 swap。** 推論服務一次只開一個；`serve-vllm.sh` 在可用記憶體低於 12 GB 時會自動停掉容器。
5. **網路。** Thor 要插有線；dashboard 的「切斷外網」按下去要記得按「恢復」（10/21 下午被誤觸過一次，斷了 3 小時）。
6. **bad-config 這張卡 Lightning 要 5–6 分鐘**（其他卡 25–95 秒）。第一幕抽卡建議拿掉它，換 upstream-down。
7. **既有成果揭露**（大致解除）：揭露頁只列 10/02 之前就有的東西。

## 6. 要做的事

**10/22 早上（到 11:00）**
1. **合併 `feat/thor-stage-up` 的 PR**（負責人：penny11124），Thor 上 `cd ~/BlackOps && git pull --ff-only`，再 `scripts/stage-up.sh`。
2. 再跑一次完整舞台彩排（`docs/demo.md` §3），其中一次從台上的筆電經會場網路操作 dashboard。
3. 錄一場最乾淨的 Thor 實跑當回放素材（`logs/runs/`），同步到兩台筆電。

**簡報要更新的地方**
- 推論頁：「vLLM + Nemotron-3.5-Lightning-30B（NVFP4，Marlin MoE）」；Bolt 是 Spark 上的對照，TensorRT-LLM 是早期基準。
- 計分卡：改引用 Thor 上 Lightning 的實測（5/5、第二幕 3/3 與秒數）。
- 拿掉「MIG 架構保證」（JetPack 7.1 沒有 MIG）與「Tier + nvpmodel 省電」；改講「在邊緣選對模型：同樣修好 5 張卡，Lightning 每 token 能耗是 Super 的四分之一」，數據在報告 §2。
- 第二幕的四個階段拿掉「模型層 + 硬體層（Tier 2、功耗模式拉高）」，保留計畫層、三把鑰匙與收尾。
- NVIDIA 整合頁補上 NeMo Guardrails 與 NeMo Agent Toolkit。

**10/22 下午**：11:00–15:00 收數據與排練；15:00–16:00 最後一次 preflight、凍結程式碼、上台前檢查。

**還等決定**
- 第一幕要不要「注入後不等，直接講投影片」。
- 舞台上要不要用 `log-injection` 這張卡，還是只在 Q&A 時用回放展示。

## 7. 文件索引

| 主題 | 檔案 |
|---|---|
| **Thor 上機流程與實測紀錄** | `docs/thor-bringup.md` |
| **Thor 上的模型與 Tier 實驗報告** | `docs/thor-tier-report-20261021.md` |
| Demo 執行手冊（舞台流程、啟動順序、失敗預案） | `docs/demo.md` |
| 三條分支整合、vLLM 取代 TRT、Spark 上的驗證 | `docs/integration-vllm.md` |
| NeMo Guardrails 與 Agent Toolkit | `docs/nemo-integration.md` |
| kb-retrieval-v2 交接文件（vanli） | `docs/HANDOFF-kb-retrieval-v2.md` |
| demo 外殼的修改紀錄與回歸 | `docs/demo-pending-edits.md` |
| 知識庫覆蓋度與機房故障 survey | `docs/kb-coverage-survey.md`、`docs/dgx-failure-knowledge-20261021.md` |
| 架構與每項設計決策 | `docs/agent-design.md` |
| 計分卡（TRT 時期） | `docs/scorecard.md` |
| 企劃書 | `docs/proposal-12-pages.md`、`docs/proposal/` |
