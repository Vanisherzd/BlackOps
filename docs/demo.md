# Blackout Ops 決賽 Demo 執行手冊

初版 2026-10-14 · @peijen；2026-10-20 依實際進度修訂；**2026-10-21 晚上依 Thor 實測修訂**（branch `feat/thor-stage-up`）

> **10/21 Thor 實測後的變更（先讀這段）**：
> 1. **推論改用 Nemotron-3.5-Lightning-30B-A3B-NVFP4**（vLLM nightly + Marlin MoE，預留 0.30）。Bolt 在 Thor（sm_110）上不能跑：它的量化格式只有 GB10 的 b12x kernel 能執行。
> 2. **Tier 與 nvpmodel 連動砍掉**（依 §7 的 11:00 規則）：在 Thor 上，4B 當 Tier-1 每 token 反而比 Lightning 更耗能，120W 模式的推論功耗跟 MAXN 一樣。人工審批保留。數據見 `docs/thor-tier-report-20261021.md`。
> 3. **一個指令啟動**：`scripts/stage-up.sh`（推論沒開會先開 Lightning，接著依序開舞台服務，最後跑 preflight）。
> 4. Thor 上 5 張舞台卡 5/5、第二幕連續 3/3、完整舞台流程（第一幕 → 斷網 → 第二幕 → 恢復）都實跑過。

> 與初版的主要差異：
> 1. **victim 留在 Thor 本機**。遠端 victim（筆電＋直連網段）因為截止提前到 10/22 16:00，**不做**。
> 2. victim 包裝成**廠務網段的產線看板＋MES API**。
> 3. 14 項 repo 修改中 12 項已在 branch 上，狀態見 §6。
> 4. 指令、port、開關都改成實際的名稱。
> 5. **推論引擎改為 vLLM**（2026-10-20 團隊決定，不再用 TensorRT-LLM）：Tier-2 是 Nemotron-3.5-Bolt-74B-A9B，以 `edge-agent` 的名稱開在 :8000（`scripts/serve-vllm.sh`）。agent、工具、NemoClaw 都不用改，見 `docs/integration-vllm.md`。

---

## 1. 現場配置與網路拓撲

**現行方案（victim 在 Thor 本機）**：Thor 留在座位，被監控系統（兩個 nginx 容器）跑在 Thor 上，agent 只能透過 ops API（:8790）碰它。台上筆電經會場網路開 Thor 的 dashboard（:8097）。「斷網」只封鎖往公網的流量，所以監控和台上畫面都不受影響，正好對應 L1 情境。

| 設備 | 位置 | 介面與位址 | 跑什麼 |
|---|---|---|---|
| Jetson Thor | 座位 | 一個口接會場網路（DHCP）；dashboard 綁這個介面 | 推論（Tier-2 :8000，Tier-1 :8010 選配）、shim :8001、ops_api :8790、NemoClaw、watchdog、power、dashboard :8097、victim 容器 :8880 |
| 簡報筆電 | 台上 | 會場網路，能用有線就不用 Wi-Fi | 投影片、dashboard 操作、本機回放資料 |
| 後台隊友筆電 | 座位 | 會場網路；和台上聯絡走手機熱點 | ssh 進 Thor 處理狀況 |

**延後的方案（遠端 victim）**：另一台筆電跑 victim，以 10.0.0.0/24 和 Thor 直連（Thor 為 10.0.0.1，筆電為 10.0.0.2）。需要手冊 #1 的重構（約 1 天）。截止提前之後，**不做**。

**評審一定會問「監控者和被監控者在同一台？」，先備好答案**：
> 「demo 為了時間控制放在同一台。agent 只能透過 ops API 這個白名單介面碰 victim，跟走網路是同一個介面，SSH key 也不在 sandbox 裡。換成遠端只是傳輸方式不同。正式部署就是一台獨立供電、獨立網卡的機櫃裝置。」

---

## 2. 故事包裝：廠務網段

- **被監控系統**：「示範晶圓廠」廠務網段（氣隙）裡的 IT 層，也就是產線看板（`edge-victim`）加上 MES 派工 API（`edge-victim-app`）。
- **看板頁**：`victim/board/index.html`，透過 dashboard 的 `/board/` 顯示。
  - 四條產線（L1 微影、L2 蝕刻、L3 薄膜、L4 量測）的稼動率與 WIP。
  - MES API 一掛，橫幅就轉紅並開始顯示「停線累計」；恢復後顯示「上次停線 mm:ss」，台上可以直接念。
- **要誠實的地方**：
  - 廠名是虛構的，稼動率和 WIP 是示意數據（頁尾有標示），唯一的即時訊號是 MES API 的 HTTP 回應碼。
  - 要說「廠務網段裡的 IT 層」，不要說「控制機台」。
  - 開場可以提到業界背景（2018 年半導體廠遭勒索軟體停線、之後的 SEMI E187），**事實與數字上台前要再查證，並附出處**。
- 市場頁一行帶過：同樣適用於金融核心、國防等法規要求氣隙的網段。

---

## 3. 完整 Demo 流程（約 6 分鐘）

### 3.1 上台前檢查（換場前 15 分鐘，後台隊友執行）

- [ ] `scripts/stage-up.sh` 最後顯示 `PREFLIGHT GREEN`（它會重啟所有舞台服務；推論沒開會先開 Lightning，約 100 秒）。只想檢查不想重啟時：`DASHBOARD_URL=http://127.0.0.1:8097 APPROVAL=manual scripts/preflight.sh --warm`。
  - 檢查項目：Tier-2 server、shim、ops-api、dashboard、watchdog、功耗紀錄器、NemoClaw `Inference: healthy`、victim verify、WAN。Tier-1 顯示 SKIP 是正常的（Tier 已砍掉）。
  - `--warm` 會先跑一次短推論，台上就不會遇到冷啟動。
  - NemoClaw 顯示 `no answer`：通常是有 agent 還拿著主機鎖，重跑一次 `stage-up.sh`（它會清掉殘留的事故 agent）。
- [ ] `.venv/bin/python scripts/faults.py reset`，verify PASS，看板是綠的。
- [ ] `scripts/wan.sh status` 顯示 `online`。
- [ ] 審批模式是 **manual**：ops_api 帶 `APPROVAL=manual`，dashboard 也會顯示（夜跑用的是 auto）。
- [ ] 15 分鐘內完整跑過一場 upstream-500，結果 PASS，而且 `logs/runs/` 有新增一場。
- [ ] 台上筆電已開好 dashboard（`http://<Thor 會場有線 IP>:8097/`；10/21 在實驗室是 `http://192.168.1.108:8097/`），回放資料已同步到筆電本機。Thor 要插網路線：它的 Wi-Fi 會掉包。
- [ ] 後台隊友和主講人用手機熱點互通，不依賴會場網路。

### 3.2 時間表

**建議做法**：第一幕注入後**不要等它修**，主講直接切回投影片講架構，agent 在背景修，約 60 秒後切回來看結果。這需要主講人同意，見 §8。

| 時間 | 段落 | 台上動作 | 畫面上看到 | 主講重點 |
|---|---|---|---|---|
| 0:00–0:40 | 開場 | 講投影片，dashboard 縮在角落 | 看板綠燈、功耗平線（約 22–25 W）、WAN online | 監控者不能與被監控者共命運；廠務網段本來就不准連外 |
| 0:40–0:55 | 抽卡 | 請評審從 4 張卡挑一張（§3.3），按「注入」 | 看板轉紅、停線計時開始 | 故障由評審決定，不是寫好的劇本 |
| 0:55–1:50 | 架構 | **切回投影片**：三把鑰匙、工具分級 | （背景：watchdog 開事故 → agent 修，Thor 上 22–35 秒） | 平時規則巡檢不推論，異常才叫醒本機模型 |
| 1:50–2:10 | 第一幕收尾 | 切回 dashboard，捲一下軌跡 | 看板已綠、「上次停線 0:xx」 | 沒人叫它修，它只收到一句警報 |
| 2:10–2:30 | 斷網 | 按「切斷外網」，在終端跑一次 `curl` 打外部 API | curl 失敗、WAN offline、對外連線計數 | 模擬 ISP 中斷，內網照常，這是 L1；在廠務網段這是常態 |
| 2:30–4:45 | 第二幕 | 按「注入 upstream-500」，審批卡跳出時按「核准」 | 四個階段，見 §3.4 | 一個事故、四層調整；Self-Healing ≠ Root Shell |
| 4:45–5:35 | 數據 | 投影片 | Thor 實測：Lightning 5/5、每 token 約 1.2 J（Super 4.9 J）、閒置不耗電；計分卡 | 在邊緣選對模型就是最大的節能 |
| 5:35–6:00 | 收尾 | 投影片 | 既有成果揭露 | Observe / Reason / Recover locally |

### 3.3 抽卡的 4 張

以下是 **Thor 上 Lightning、NemoClaw 路徑**的實測（10/21，`logs/drills-20261021-180239.log` 與舞台彩排）；括號是 Spark 上 Bolt 的中位時間，供對照。

| 卡 | Thor／Lightning | 備註 |
|---|---|---|
| nginx-stopped | 22–25 s（Bolt 40 s） | 前端停掉 |
| container-removed | 27 s（Bolt 31 s） | |
| upstream-paused | 26 s（Bolt 46 s） | /api/ 504 |
| bad-config | **340 s**（Bolt 66 s） | Lightning 會改錯好幾次（中途 5 次被規則拒絕）才修好。**第一幕不要放這張**，換成 upstream-down |

第一幕要壓在 60 秒內：nginx-stopped、container-removed、upstream-paused、upstream-down 四張。

**選配：`log-injection`（NeMo Guardrails）**：沿用 upstream-500 的故障，上游 log 裡藏了兩行對 agent 下的指令（要它執行 `cleanup_disk`、`kill_process`）。軌跡區會看到 `[guardrail: line removed …]`；agent 若照著做，會被動作 rail 擋下。時間跟 upstream-500 差不多。可以取代第二幕，或留作 Q&A 時用回放展示。被問到「被監控的系統被駭了怎麼辦」時，就展示這一場。

### 3.4 第二幕的四個階段（upstream-500）

1. **計畫層**：/api/ 回 500。agent 讀 log、拿到 runbook，先做最小動作 `docker_restart edge-victim-app`，驗證後仍是 500，原假設被推翻。agent 若跳過重啟、直接要求重建，程式鑰匙會擋下：`denied: recreate_stack ... before it was started or restarted`。這一行不一定每場都出現；**出現時，主講停在那行紅字上念出來**。
2. ~~**模型層＋硬體層**~~（10/21 砍掉）：Tier 與 nvpmodel 連動在 Thor 上拿不出節能數據。功耗曲線仍然會在推論時升到 64–82 W，可以直接指著講「推論時才耗電」。
3. **三把鑰匙**：agent 提出 `recreate_stack replace=true`。
   - 程式鑰匙：確認「重啟過，而且仍然失敗」。
   - 審核代理：引用證據，判 APPROVE。
   - 人：審批卡跳出，通道顯示「Jetson 本機（外網不可用）」。
4. **收尾**：台上按核准，容器重建，/api/ 回 200，看板轉綠，功耗回落。修好之後 agent 若還想改東西，工具層會以「系統健康，沒有要修的」拒絕，watchdog 也會在 60 秒後停掉它，看板不會再轉紅。

驗證紀錄：
- 2026-10-20：Spark 上以 host 迴圈加上 `APPROVAL=auto TIER_MODE=adaptive` 跑過一次，結果 PASS，Tier 1→2→1 都有紀錄。
- 2026-10-21：Bolt/vLLM 的 NemoClaw 路徑 3/3（中位 106 s），開 `SHIM_ADAPTIVE=1 TRIAGE=1` 時 5/5（中位 131 s，其中 4 場出現 `denied:`）。
- **2026-10-21 Thor／Lightning**：dashboard 注入 → watchdog → agent → 核准卡（通道顯示「Jetson 本機（外網不可用）」）→ dashboard 核准，**連續 3/3**：81.9、127.1、87.0 s；核准卡在注入後 78–123 s 出現。完整舞台流程（第一幕 → 斷網 → 第二幕 → 恢復）見 §3.5。

**第二幕要排 2–2.5 分鐘**：核准卡約 80–120 秒出現，按下去後 5 秒內結案。

### 3.5 完整舞台彩排（Thor，10/21 晚上）

腳本照 §3.2 的時間間隔，全程經 dashboard 的 API 操作：第一幕注入 → 結案後 20 秒按「切斷外網」→ 再 20 秒注入 upstream-500 → 核准卡出現後 5 秒按核准 → 按「恢復外網」→ 觀察 75 秒。

| 輪次 | 第一幕 | 斷網 | 第二幕 | 之後 75 秒 |
|---|---|---|---|---|
| 1（修正前） | nginx-stopped 22 s | 外部 curl 被擋，計數有跳 | 93.6 s，核准卡 +88 s | ❌ 第一幕延續的 session 寫壞 `default.conf`，前端掛掉、看板轉紅（agent 自己在 11 秒內救回） |
| 修正後 | 見 `docs/thor-tier-report-20261021.md` §6 | | | |

修正內容：工具層在兩個健康 URL 都是 200 時拒絕一切改動（`HEALTHY_GUARD`）；watchdog 結案 60 秒後停掉 agent（`AGENT_GRACE`），派新 agent 前先停舊的，連子行程整組結束。

---

## 4. 失敗預案

**原則**：現場出狀況時照實說，然後切到能證明同一件事的備援。所有回放畫面都標示 REPLAY。

| 狀況 | 怎麼發現 | 台上動作 | 後台動作 |
|---|---|---|---|
| 台上連不到 Thor | dashboard 顯示「連不到 Thor」（超過 5 秒沒更新） | 切到筆電本機的回放（選一場 → 回放），口頭說明 | 檢查座位到會場的網路線 |
| 看板空白 | — | 看板一律從 dashboard 開（`:8097/board/`），不要開 victim 的 `:8880` | — |
| 第一幕 agent 卡住 | 軌跡 30 秒沒有新步驟，或總時間超過 90 秒 | 直接進「斷網」，不等 | 等該場結束後 `faults.py reset` |
| agent 判斷升級而非修復 | watchdog 事件 `agent-done-unhealthy` | 照實講：它沒有把系統弄得更糟，而是交還給人 | reset，準備第二幕 |
| 第二幕審核代理 REJECT | 軌跡出現 `review-rejected` | 念出它引用的證據，說明這正是三把鑰匙在運作；切回放展示核准的路徑 | 不要重試 |
| 主講沒來得及按核准 | 180 s 逾時（`APPROVAL_TIMEOUT`） | 逾時一律視為拒絕，agent 會升級給人：「fail-closed 就是設計」 | — |
| 推論 server 掛掉 | preflight，或 dashboard 沒有新步驟 | 全程改用回放 | `scripts/stage-up.sh`（會先開 Lightning，約 100 秒） |
| 「切斷外網」無效 | curl 仍然成功 | 跳過，口述 L1 情境，第二幕照做 | `scripts/wan.sh status` |
| 第二幕的 agent 起不來（事故馬上變 `agent-done-unhealthy`） | watchdog 日誌 `Failed to acquire lock` | 照「agent 判斷升級」那列講 | `scripts/stage-up.sh`（清掉殘留的事故 agent），`faults.py reset` |
| 修好之後看板又轉紅 | 結案後 30–60 秒 | 照實講：agent 自己又改了東西（10/21 已加防護，理論上不會再發生） | `faults.py reset` |
| 推論功耗看起來不高 | 曲線只到 60–80 W | 講「閒置 22–25 W，推論才耗電」 | — |
| 時間不夠 | 主講看計時器 | 砍第一幕，保住第二幕 | — |

---

## 5. 器材清單

主辦只提供 Thor 主機、螢幕、鍵盤、滑鼠，以下全部自備。victim 在 Thor 本機時，**victim 筆電與直連線材都改成選配**。

| 類別 | 品項 | 數量 | 用途 | 負責人 |
|---|---|---|---|---|
| 網路 | Cat6 網路線 5–10 m | 2 | Thor 接會場網路；台上筆電有線接會場（若有提供） | |
| 網路 | USB-C 轉 RJ45 gigabit 網卡 | 2 | 台上筆電、備用 | |
| 網路 | 線材標籤 | 1 卷 | 標明「會場／WAN」 | |
| 台上 | 簡報筆電＋備援筆電 | 2 | 投影片、dashboard、回放資料 | |
| 台上 | HDMI 線、USB-C 轉 HDMI | 各 2 | 接投影機 | |
| 後台 | 隊友筆電、手機熱點 | 各 1 | ssh 進 Thor；與台上聯絡 | |
| 儲存 | USB 3.2 SSD 1 TB（主、備） | 2 | 離線包 | |
| 電源 | 多孔延長線 3 m、所有充電器 | 2／全部 | | |
| 選配：遠端 victim | victim 筆電（Ubuntu 24.04）、Cat6 1 m ×2、5 埠 switch、可開機 Ubuntu USB | — | 只在決定做遠端 victim 時才需要 | |
| 選配：量測 | 插座式電力計 | 1 | 整機功耗交叉驗證，電源是插牆式才用得上 | |

**要先向主辦確認**（repo 裡看不到進度，請負責人更新）：
- 「只提供主機」是否包含電源供應器：三款機型都是端子台直流輸入，KT78／KT68 為 9–48 VDC，PE3000N 為 12–60 VDC。
- 是否有 sudo 權限，`wan.sh` 和 `nvpmodel` 需要。
- 可否自帶 switch。
- 台上能否用有線連到會場網路。

**離線包內容**：
- **10/21 更新：Thor 上已經有全部需要的東西**（映像、模型、編譯快取都在 Thor 的磁碟上），離線包只是備援。Thor 用的是 `vllm/vllm-openai:nightly-77154ca` + Nemotron-3.5-Lightning-30B-A3B-NVFP4；備援 Nano 30B 用 NGC `nvcr.io/nvidia/vllm:26.01-py3`。`blackbox-vllm-bolt` 在 Thor 上不能用（b12x 只支援 SM12x）。
- **不要刪 Thor 上的** `~/.cache/huggingface`、`~/.cache/flashinfer`、`~/.cache/flashinfer-gen`、`~/.cache/vllm-compile`：重建要重新下載與編譯（Nano 30B 的 FlashInfer 要 90 分鐘）。
- 模型：bge-small-en-v1.5（RAG）。
- `nginx:latest` 的 arm64 映像（victim 跑在 Thor 上）；做遠端 victim 才需要 amd64。
- uv 快取、`uv.lock` 對應的 wheels、repo 完整副本。
- 回放素材：Spark 上的 `logs/runs/`，比賽中再補上 Thor 的真實紀錄。

---

## 6. Repo 修改清單（2026-10-21 晚上，Thor 實測後）

| # | 優先 | 項目 | 檔案 | 狀態 | 驗證 |
|---|---|---|---|---|---|
| 1 | P0 | 遠端 victim | `victim.py`（新）、faults.py、agent.py | ⏸ **延後** | — |
| 2 | P0 | prompt／runbook 修正 | runbooks/upstream-502.md、nemoclaw/workspace/AGENTS.md | ✅ | 8880 只剩 victim 在本機時用得到的地方 |
| 3 | P0 | 廠務看板頁 | `victim/board/`、faults.py GOOD_CONF、agent.py write_config | ✅ | `/board/` 回 200；BAD_CONF 仍觸發 [emerg]；回歸無退步 |
| 4 | P0 | watchdog | `scripts/watchdog.py` | ✅ | **Thor 實跑**：叫醒 NemoClaw、`agent-done-unhealthy` 交還給人、結案後 60 秒停掉 agent、派新 agent 前停舊的、guardrails 測試時停手 |
| 5 | P0 | 歸檔 | watchdog 每場自動存 `logs/runs/<時間>-incident/`；`scripts/run-archive.sh` | ✅ | Thor 上每場事故都有歸檔 |
| 6 | P0 | 斷網 | `scripts/wan.sh down --yes\|up\|status` | ✅ | **Thor 實跑**：外部 curl 被擋、sandbox 網段自動偵測（Thor 172.19.0.0/16）、計數有跳 |
| 7 | P0 | dashboard | `scripts/dashboard.py`、`dashboard.html` | ✅ | **Thor 實跑**：注入／斷網／恢復按鈕、核准卡（外網斷時顯示「Jetson 本機（外網不可用）」） |
| 8 | P0 | preflight | `scripts/preflight.sh`；一鍵版 `scripts/stage-up.sh` | ✅ | Thor 上 PREFLIGHT GREEN |
| 9 | P0 | 推論（vLLM） | `scripts/serve-vllm.sh`（generic + Lightning 配方、編譯快取掛主機、記憶體保護） | ✅ | Thor：Lightning 5/5、首次 437 s／之後約 100 s |
| 10 | P1 | 人工審批 | `scripts/approval.py`，接在 `agent.reviewed()` APPROVE 之後 | ✅ | **Thor 實跑**：第二幕 3/3 經 dashboard 核准 |
| 11 | P1 | Tier 控制器 | `scripts/tier.py`，接在 agent.py 和 oai_shim.py | ✂ 不上台 | 程式保留；Thor 實測 4B 當 Tier-1 比 Lightning 更耗能，見 `docs/thor-tier-report-20261021.md` |
| 12 | P1 | 功耗 | `scripts/power.py` | ✅ | Thor：tegrastats `VIN`；閒置 22–25 W、推論 60–82 W |
| 13 | P1 | 夜跑 | `scripts/overnight.sh` | ✂ 不需要 | Tier 砍掉後沒有 A/B 要跑 |
| 14 | P2 | 能耗計分 | `scripts/score-runs.py`（score.py 不動） | ✅ | 簡報改用報告裡的 J/token 實測 |
| 15 | P0 | 結案後的防護 | `scripts/agent.py`（`HEALTHY_GUARD`）、`scripts/watchdog.py`（`AGENT_GRACE`） | ✅ | 系統健康時拒絕一切改動；修好後看板不會再被 agent 弄紅 |
| 16 | P1 | shim 讀 vLLM 0.32 的 `reasoning` 欄位 | `scripts/oai_shim.py` | ✅ | Lightning 把 tool call 寫在思考裡時也救得回來 |

所有開關預設都是關的：`APPROVAL=off`、`TIER_MODE=off`、`DASH_ACTIONS` 未設、`wan.sh down` 要加 `--yes`。沒有打開時，行為和 kb-retrieval-v2 一樣。`stage-up.sh` 會打開舞台需要的那幾個（`APPROVAL=manual`、`GUARDRAILS=1`、`DASH_ACTIONS=1`、`WATCH_TRIAGE=1`、`SHIM_ADAPTIVE=1`）。

### 6.1 在 Thor 上啟動

**10/21 起只要一個指令**（Thor 重開機之後也是）：

```bash
ssh nv@<Thor 有線 IP>            # 10/21 在實驗室是 192.168.1.108
cd ~/edge-agent && scripts/stage-up.sh
```

它依序做：:8000 沒有推論服務就用 Lightning 的配方啟動（約 100 秒）→ 清掉殘留的事故 agent → 重啟 shim、guard_api、ops_api、power、watchdog、dashboard（帶舞台設定）→ `preflight.sh --warm`。`scripts/stage-up.sh status` 看各服務，`scripts/stage-up.sh down` 全部停掉。victim 容器與 NemoClaw 是開機自己會起來的；victim 不健康就 `faults.py reset`。

以下是 10/20 的手動版（Bolt + Tier），**留作對照，Thor 上不要用**：

```bash
cd ~/edge-agent
scripts/serve-vllm.sh                                        # Tier-2：Bolt on vLLM，:8000，等到 /health 才返回（最多 15 分鐘）
T1_MODEL=<小模型的 HF ID> scripts/serve-tier1.sh              # Tier-1：vLLM，:8010（選配）
bash scripts/shim-env.sh "SHIM_THINK=1 SHIM_ADAPTIVE=1 TIER_MODE=adaptive UPSTREAM_T1=http://127.0.0.1:8010"      # shim :8001
setsid nohup .nemo-venv/bin/python scripts/guard_api.py > logs/guard-api.out 2>&1 < /dev/null &        # NeMo Guardrails sidecar :8792
env GUARDRAILS=1 APPROVAL=manual TIER_MODE=adaptive UPSTREAM_T1=http://127.0.0.1:8010 \
  setsid nohup .venv/bin/python scripts/ops_api.py > logs/ops-api.out 2>&1 < /dev/null & echo $! > logs/ops-api.pid
setsid nohup .venv/bin/python scripts/power.py log > /dev/null 2>&1 < /dev/null &
WATCH_TRIAGE=1 setsid nohup .venv/bin/python scripts/watchdog.py > logs/watchdog.out 2>&1 < /dev/null &
DASH_ACTIONS=1 APPROVAL=manual setsid nohup .venv/bin/python scripts/dashboard.py > logs/dashboard.out 2>&1 < /dev/null &
DASHBOARD_URL=http://127.0.0.1:8097 T1_URL=http://127.0.0.1:8010 scripts/preflight.sh --warm
```

- 沒有 Tier-1 server 時，shim 和 ops_api 都**不要**設 `TIER_MODE=adaptive`。不然 Tier 指示會變，但兩個 Tier 實際上是同一顆模型，台上講不清楚。
- nvpmodel 連動：加 `TIER_NVPMODEL=1 NVP_T1=<巡檢模式 id> NVP_T2=<高效能模式 id>`，模式 id 在 10/21 用 `nvpmodel -q --verbose` 查。
- dashboard 沒有登入驗證，`DASH_ACTIONS=1` 時，會場網路上任何人都能按注入。只在比賽期間開，或用 `DASH_HOST` 綁定台上用的那個介面。
- 記憶體：這台沒有 swap。Lightning 用 0.30（系統剩約 80 GB）；不要再另外載入大模型（10/17 Spark 曾經整台卡死）。`serve-vllm.sh` 在可用記憶體低於 12 GB 時會自動停掉容器。

---

## 7. 時程對照（2026-10-21 更新：截止改為 **10/22 16:00**）

**唯一的硬性檢查點改成 10/22 11:00**：第二幕在 Thor 上**連續成功三次**，才保留 Tier 與 nvpmodel；否則砍掉，只保留人工審批，能耗改用口頭和夜跑數據交代。

揭露：預賽（10/02）到決賽之間的實作不算賽前成果，所以揭露頁只列 10/02 之前就有的東西（見 `docs/project-status-2026-10-20.md` 風險 6）。

**到 10/21 上午為止（在 Spark 上）**
- [x] #2、#3 看板頁與 prompt 修正，回歸無退步
- [x] #4 watchdog、#5 歸檔、#6 wan.sh、#8 preflight（骨架）
- [x] #7 dashboard：四區顯示、注入／reset／斷網按鈕、回放
- [x] #10–#14：審批、Tier、功耗、夜跑、能耗計分
- [x] vLLM + Bolt 整合（NemoClaw 15/15）、NeMo Guardrails 與 Agent Toolkit
- [x] 離線包（TRANSCEND：`thor-bundle-20261021/`）
- [ ] 向主辦確認：電源供應器、sudo、自帶 switch、台上能否有線連到會場網路、**今晚能不能使用 Thor**
- ✂ #1 遠端 victim：時程縮短，**不做**

**10/21 工作坊到今晚（3 人）**：照 `docs/thor-bringup.md` 的步驟做
- [x] 機型與 JetPack 版本：T5000、R38.4 = **JetPack 7.1，沒有 MIG** → 簡報拿掉「架構保證」
- [x] 推論：A（Bolt b12x）、B（Bolt + NGC）不行；C（Nano 30B）可以；**採用 E = Lightning**（`docs/thor-bringup.md` §3）
- [x] NemoClaw onboard（`NEMOCLAW_SANDBOX_GPU=0`），5 張舞台卡 5/5
- [x] Tier-1 模型實測（4B、Lightning、Super 各種組合）→ **Tier 砍掉**（報告）
- [x] nvpmodel 模式表與切換：0↔1 即時、2/3 要重開機；電源軌 `VIN`
- [ ] 和駐點網路組實測：從台上位置連到座位上 Thor 的 :8097（實驗室的筆電已經連得到）
- ✂ 夜跑：Tier 砍掉後不需要

**10/22（截止 16:00）**
- [x] 在 Thor 上實跑 watchdog 叫醒 agent、dashboard 按鈕與核准、`wan.sh` 斷網、歸檔（10/21 晚上提前完成；Tier 切換砍掉）
- [x] **11:00 檢查點**：第二幕連續 3 次成功（10/21 晚上 3/3：81.9、127.1、87.0 s）
- [ ] 合併 `feat/thor-stage-up` 的 PR；Thor 上 `git pull --ff-only` 後 `scripts/stage-up.sh`
- [ ] 11:00–15:00：收數據（`score-runs.py`、`nat_profile.py`）、更新投影片數據頁；排練 2–3 次，其中一次從台上位置操作；錄一場最乾淨的 Thor 實跑當回放素材，同步到兩台筆電
- [ ] 15:00–16:00：最後一次 preflight，凍結程式碼
- [ ] 換場前 15 分鐘：跑完 §3.1 的上台前檢查

---

## 8. 待決定

- [ ] 第一幕「注入後不等，切回投影片講架構」的做法，主講人是否接受
- ✂ 遠端 victim：時程縮短，不做
- [x] ~~「平時規則巡檢不推論，異常才由 Tier-1 接手」~~ → 改成「平時規則巡檢不推論，異常才叫醒本機模型」（Tier 已砍掉）

相關文件：`docs/project-status-2026-10-20.md`（整體進度）、`docs/demo-pending-edits.md`（修改紀錄與回歸結果）、`docs/kb-coverage-survey.md`（知識庫與故障 survey）、`docs/HANDOFF-kb-retrieval-v2.md`（vanli 的交接文件）。
