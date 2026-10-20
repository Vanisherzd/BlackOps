# Blackout Ops 決賽 Demo 執行手冊

初版 2026-10-14 · @peijen；**2026-10-20 依實際進度修訂**（branch `demo-infra`）

> 與初版的主要差異：
> 1. **victim 先留在 Thor 本機**，遠端 victim（筆電＋直連網段）延後到 10/22 傍晚檢查點之後再決定。
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

**延後的方案（遠端 victim）**：另一台筆電跑 victim，以 10.0.0.0/24 和 Thor 直連（Thor 為 10.0.0.1，筆電為 10.0.0.2）。需要手冊 #1 的重構（約 1 天，見 §6）。10/22 傍晚第二幕穩定後才評估要不要做。

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

- [ ] `DASHBOARD_URL=http://127.0.0.1:8097 T1_URL=http://127.0.0.1:8010 scripts/preflight.sh --warm` 顯示 `PREFLIGHT GREEN`。
  - 檢查項目：Tier-2／Tier-1 server、shim、ops-api、dashboard、watchdog、功耗紀錄器、NemoClaw `Inference: healthy`、victim verify、WAN。
  - 沒有 Tier-1 server 時不設 `T1_URL`，該項會顯示 SKIP。
  - `--warm` 會讓每顆模型各跑一次短推論，台上就不會遇到冷啟動。
- [ ] `.venv/bin/python scripts/faults.py reset`，verify PASS，看板是綠的。
- [ ] Tier 顯示 Tier-1：`tier.py status`；有開 nvpmodel 連動的話，確認在巡檢模式。
- [ ] `scripts/wan.sh status` 顯示 `online`。
- [ ] 審批模式是 **manual**：ops_api 帶 `APPROVAL=manual`，dashboard 也會顯示（夜跑用的是 auto）。
- [ ] 15 分鐘內完整跑過一場 upstream-500，結果 PASS，而且 `logs/runs/` 有新增一場。
- [ ] 台上筆電已開好 dashboard（`http://<Thor 會場 IP>:8097/`），回放資料已同步到筆電本機。
- [ ] 後台隊友和主講人用手機熱點互通，不依賴會場網路。

### 3.2 時間表

**建議做法**：第一幕注入後**不要等它修**，主講直接切回投影片講架構，agent 在背景修，約 60 秒後切回來看結果。這需要主講人同意，見 §8。

| 時間 | 段落 | 台上動作 | 畫面上看到 | 主講重點 |
|---|---|---|---|---|
| 0:00–0:40 | 開場 | 講投影片，dashboard 縮在角落 | 看板綠燈、功耗平線、Tier-1、WAN online | 監控者不能與被監控者共命運；廠務網段本來就不准連外 |
| 0:40–0:55 | 抽卡 | 請評審從 4 張卡挑一張（§3.3），按「注入」 | 看板轉紅、停線計時開始 | 故障由評審決定，不是寫好的劇本 |
| 0:55–1:50 | 架構 | **切回投影片**：三把鑰匙、Tier、工具分級 | （背景：watchdog 開事故 → agent 修） | 平時規則巡檢不推論，異常才由 Tier-1 接手 |
| 1:50–2:10 | 第一幕收尾 | 切回 dashboard，捲一下軌跡 | 看板已綠、「上次停線 0:xx」 | 沒人叫它修，它只收到一句警報 |
| 2:10–2:30 | 斷網 | 按「切斷外網」，在終端跑一次 `curl` 打外部 API | curl 失敗、WAN offline、對外連線計數 | 模擬 ISP 中斷，內網照常，這是 L1；在廠務網段這是常態 |
| 2:30–4:30 | 第二幕 | 按「注入 upstream-500」，審批卡跳出時按「核准」 | 四個階段，見 §3.4 | 一個事故、四層調整；Self-Healing ≠ Root Shell |
| 4:30–5:30 | 數據 | 投影片 | 夜跑結果：有調度 vs 全程 Tier-2 的 Wh、Tier-1 承接比例、計分卡 | 算力是 Agent 自己要管的資源 |
| 5:30–6:00 | 收尾 | 投影片 | 既有成果揭露 | Observe / Reason / Recover locally |

### 3.3 抽卡的 4 張

以下是 Spark 上 NemoClaw 版計分卡的中位時間（`docs/scorecard.md`）。vanli 的分情況思考加開場資料（`SHIM_ADAPTIVE=1 TRIAGE=1`）可以把見過題目的中位時間從 56 秒壓到 38 秒。

| 卡 | 中位時間 | 備註 |
|---|---|---|
| nginx-stopped | 約 44 s | 前端停掉 |
| container-removed | 約 30 s | 最快 |
| upstream-paused | 約 30–46 s | /api/ 504 |
| bad-config | 約 79 s | 會讀到 `[emerg]` 再改設定，最好看但最慢 |

第一幕要壓在 60 秒內的話，把 bad-config 換成 upstream-down（約 36 s）。

### 3.4 第二幕的四個階段（upstream-500）

1. **計畫層**：/api/ 回 500。agent 讀 log、拿到 runbook，先做最小動作 `docker_restart edge-victim-app`，驗證後仍是 500，原假設被推翻。agent 若跳過重啟、直接要求重建，程式鑰匙會擋下：`denied: recreate_stack ... before it was started or restarted`。這一行不一定每場都出現；**出現時，主講停在那行紅字上念出來**。
2. **模型層＋硬體層**：重啟後仍失敗 → `tier.set_tier(2)`，Tier 指示變成 Tier-2。有開 nvpmodel 連動的話，功耗模式會拉高，曲線出現尖峰。
3. **三把鑰匙**：agent 提出 `recreate_stack replace=true`。
   - 程式鑰匙：確認「重啟過，而且仍然失敗」。
   - 審核代理：引用證據，判 APPROVE。
   - 人：審批卡跳出，通道顯示「Jetson 本機（外網不可用）」。
4. **收尾**：台上按核准，容器重建，/api/ 回 200，看板轉綠，Tier 降回 1，功耗回落。

2026-10-20 在 Spark 上以 host 迴圈加上 `APPROVAL=auto TIER_MODE=adaptive` 跑過一次，結果 PASS，Tier 1→2→1 都有紀錄。**NemoClaw 路徑上的這一幕還沒驗證。**

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
| 推論 server 掛掉 | preflight，或 dashboard 沒有新步驟 | 全程改用回放 | 重啟 serve 腳本，約 2 分鐘 |
| 「切斷外網」無效 | curl 仍然成功 | 跳過，口述 L1 情境，第二幕照做 | `scripts/wan.sh status`（需要 sudo） |
| 沒有 Tier-1 server 或 nvpmodel 沒切換 | 功耗沒有尖峰 | 口頭帶過，用夜跑數據頁補 | — |
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
- Thor 可用的 vLLM 映像：先試 `blackbox-vllm-bolt:77154ca`（24.7 GB，含 b12x）。它是為 GB10（sm121）建的，Thor 能不能跑**未驗證**；退路是 jetson-containers 的 vLLM r38。
- 模型：Nemotron-3.5-Bolt-74B-A9B-24GB（Tier-2，24.1 GB）；Nemotron-3-Nano-30B-A3B-NVFP4（64 GB 機型的 Tier-2 退路）；Tier-1 的小模型（**HF ID 要到現場確認**，`serve-tier1.sh` 用 `T1_MODEL` 指定）；bge-small-en-v1.5（HF cache tar）。
- `nginx:latest` 的 arm64 映像（victim 跑在 Thor 上）；做遠端 victim 才需要 amd64。
- uv 快取、`uv.lock` 對應的 wheels、repo 完整副本。
- 回放素材：Spark 上的 `logs/runs/`，比賽中再補上 Thor 的真實紀錄。

---

## 6. Repo 修改清單（2026-10-20 狀態，branch `demo-infra`）

| # | 優先 | 項目 | 檔案 | 狀態 | 驗證 |
|---|---|---|---|---|---|
| 1 | P0 | 遠端 victim | `victim.py`（新）、faults.py、agent.py | ⏸ **延後** | — |
| 2 | P0 | prompt／runbook 修正 | runbooks/upstream-502.md、nemoclaw/workspace/AGENTS.md | ✅ | 8880 只剩 victim 在本機時用得到的地方 |
| 3 | P0 | 廠務看板頁 | `victim/board/`、faults.py GOOD_CONF、agent.py write_config | ✅ | `/board/` 回 200；BAD_CONF 仍觸發 [emerg]；回歸無退步 |
| 4 | P0 | watchdog | `scripts/watchdog.py` | ✅ 骨架 | 以假 server 測過「開事故→恢復→歸檔」；**實際叫醒 NemoClaw 未測** |
| 5 | P0 | 歸檔 | `scripts/run-archive.sh`（包住 drill，不改 drill） | ✅ | **未實跑** |
| 6 | P0 | 斷網 | `scripts/wan.sh down --yes\|up\|status` | ✅ 骨架 | 只驗了語法；需要 sudo，**不要在共用的 Spark 上跑 down** |
| 7 | P0 | dashboard | `scripts/dashboard.py`、`dashboard.html` | ✅ | 各端點測過；已能從瀏覽器開啟（經 VS Code 轉 port），**版面待回饋** |
| 8 | P0 | preflight | `scripts/preflight.sh` | ✅ | 在 Spark 上跑過 |
| 9 | P0 | 推論（vLLM） | `scripts/serve-vllm.sh`（Bolt，:8000，Jetson 上自動改用 `--runtime nvidia`）、`scripts/serve-tier1.sh`（vLLM，:8010） | ⚠️ 已準備 | Spark 上 Bolt＋agent 的回歸**待跑**；Thor **只能到現場驗證** |
| 10 | P1 | 人工審批 | `scripts/approval.py`，接在 `agent.reviewed()` APPROVE 之後 | ✅ | manual／逾時／off／auto 都測過 |
| 11 | P1 | Tier 控制器 | `scripts/tier.py`，接在 agent.py 和 oai_shim.py | ✅ | host 迴圈下 1→2→1；**4B 和 30B 的 parser 相容性未測** |
| 12 | P1 | 功耗 | `scripts/power.py` | ✅ | Spark 上用 nvidia-smi 測過；Thor 用 tegrastats，rail 名稱要到現場確認 |
| 13 | P1 | 夜跑 | `scripts/overnight.sh` | ✅ | **未實跑** |
| 14 | P2 | 能耗計分 | `scripts/score-runs.py`（score.py 不動） | ✅ | 以合成資料驗算 |

所有開關預設都是關的：`APPROVAL=off`、`TIER_MODE=off`、`DASH_ACTIONS` 未設、`wan.sh down` 要加 `--yes`。沒有打開時，行為和 kb-retrieval-v2 一樣。

### 6.1 在 Thor 上啟動（照這個順序）

```bash
cd ~/edge-agent
scripts/serve-vllm.sh                                        # Tier-2：Bolt on vLLM，:8000，等到 /health 才返回（最多 15 分鐘）
T1_MODEL=<小模型的 HF ID> scripts/serve-tier1.sh              # Tier-1：vLLM，:8010（選配）
bash scripts/shim-env.sh "SHIM_THINK=1 SHIM_ADAPTIVE=1 TIER_MODE=adaptive UPSTREAM_T1=http://127.0.0.1:8010"      # shim :8001
env APPROVAL=manual TIER_MODE=adaptive UPSTREAM_T1=http://127.0.0.1:8010 \
  setsid nohup .venv/bin/python scripts/ops_api.py > logs/ops-api.out 2>&1 < /dev/null & echo $! > logs/ops-api.pid
setsid nohup .venv/bin/python scripts/power.py log > /dev/null 2>&1 < /dev/null &
WATCH_TRIAGE=1 setsid nohup .venv/bin/python scripts/watchdog.py > logs/watchdog.out 2>&1 < /dev/null &
DASH_ACTIONS=1 APPROVAL=manual setsid nohup .venv/bin/python scripts/dashboard.py > logs/dashboard.out 2>&1 < /dev/null &
DASHBOARD_URL=http://127.0.0.1:8097 T1_URL=http://127.0.0.1:8010 scripts/preflight.sh --warm
```

- 沒有 Tier-1 server 時，shim 和 ops_api 都**不要**設 `TIER_MODE=adaptive`。不然 Tier 指示會變，但兩個 Tier 實際上是同一顆模型，台上講不清楚。
- nvpmodel 連動：加 `TIER_NVPMODEL=1 NVP_T1=<巡檢模式 id> NVP_T2=<高效能模式 id>`，模式 id 在 10/21 用 `nvpmodel -q --verbose` 查。
- dashboard 沒有登入驗證，`DASH_ACTIONS=1` 時，會場網路上任何人都能按注入。只在比賽期間開，或用 `DASH_HOST` 綁定台上用的那個介面。
- 記憶體：這台沒有 swap。Bolt 用 0.45，Tier-1 預設 0.12；不要再另外載入大模型（10/17 曾經整台卡死）。

---

## 7. 時程對照

**10/22 傍晚是唯一的硬性檢查點**：第二幕在 Thor 上**連續成功三次**，才保留 Tier 與 nvpmodel；否則砍掉，只保留人工審批，能耗改用口頭和夜跑數據交代。

**到 10/20 為止（在 Spark 上做，全部列入既有成果揭露）**
- [x] #2、#3 看板頁與 prompt 修正，回歸無退步
- [x] #4 watchdog、#5 歸檔、#6 wan.sh、#8 preflight（骨架）
- [x] #7 dashboard：四區顯示、注入／reset／斷網按鈕、回放
- [x] #10–#14：審批、Tier、功耗、夜跑、能耗計分（提前完成，見下方提醒）
- [ ] #1 遠端 victim（延後）
- [ ] 離線包兩份：Thor 推論映像、4B 模型、HF cache、wheels
- [ ] 向主辦確認：電源供應器、sudo、自帶 switch、台上能否有線連到會場網路

**10/21 工作坊（3 人）**
- [ ] 確認配到的機型：T5000 128 GB 還是 T4000 64 GB。64 GB 時，Bolt（0.45 × 64 ≈ 29 GB，權重就 24 GB）可能放不下 KV cache，改用 `MODEL_ID=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4`，或調高 `GPU_MEMORY_UTILIZATION`
- [ ] Thor 上 vLLM 能跑起來：`scripts/serve-vllm.sh`（runtime nvidia）；不行就改用 jetson-containers vLLM（`IMAGE`／`BOLT_IMAGE` 可換）；記錄峰值記憶體
- [ ] 確認 Tier-1 的 4B 模型 ID，並驗證 shim 解析它的 tool call 和 reasoning 都正常
- [ ] `nvpmodel -q --verbose` 抄下模式表，實測切換延遲、哪些模式需要重開機
- [ ] 確認 MIG 能否切給兩個推論實例；不行就把簡報裡「架構保證」的措辭改掉
- [ ] NemoClaw onboard（Thor 上帶 `NEMOCLAW_SANDBOX_GPU=0`）
- [ ] 記下 tegrastats 的電源軌名稱，必要時設 `POWER_TOTAL_RAIL`
- [ ] 和駐點網路組實測：從台上位置連到座位上 Thor 的 :8097

**10/22 黑客松第一天**
- [ ] 上午：完整部署到 Thor，`faults.py setup`，跑 9 案回歸（NemoClaw 路徑，每題至少 3 次）
- [ ] 下午：在 Thor 上驗證審批、Tier、功耗曲線、watchdog 叫醒 agent、run-archive
- [ ] 傍晚：第二幕連續三次成功的檢查點；再決定遠端 victim 要不要做
- [ ] 晚上：完整排練一次「冷開機即斷網」，抓出偷偷連外的服務
- [ ] 睡前：`UPSTREAM_T1=http://127.0.0.1:8010 scripts/overnight.sh 3` 啟動夜跑

**10/23 黑客松第二天**
- [ ] 早上：`scripts/score-runs.py` 收夜跑數據，更新投影片數據頁
- [ ] 排練三次，其中一次從台上位置實際操作
- [ ] 挑一場最乾淨的 Thor 真實演練（`logs/runs/`），同步到簡報筆電與備援筆電當回放素材
- [ ] 12:30 code freeze
- [ ] 換場前 15 分鐘：跑完 §3.1 的上台前檢查

---

## 8. 待決定

- [ ] 第一幕「注入後不等，切回投影片講架構」的做法，主講人是否接受
- [ ] 遠端 victim：10/22 傍晚再決定
- [ ] 簡報上，「平時規則巡檢不推論，異常才由 Tier-1 接手」的措辭，取代原本的「Tier-1 巡檢」

相關文件：`docs/project-status-2026-10-20.md`（整體進度）、`docs/demo-pending-edits.md`（修改紀錄與回歸結果）、`docs/kb-coverage-survey.md`（知識庫與故障 survey）、`docs/HANDOFF-kb-retrieval-v2.md`（vanli 的交接文件）。
