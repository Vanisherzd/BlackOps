# 交接文件：`kb-retrieval-v2` 分支（2026-10-13 ～ 2026-10-20）

作者：vanli（ivan711031@hsnl.cs.nthu.edu.tw）。寫於 2026-10-20 23:10，HEAD `fd7a79a`。

## 0. 先看這裡：現況與注意事項

- **分支**：`kb-retrieval-v2`，比 `main` 多 25 個 commit，2026-10-20 已 push 到 `https://github.com/Vanisherzd/BlackOps`
  （原 `dgx-spark-edge-agent`）。Spark 上的工作目錄是 `hsnl@192.168.2.60:~/edge-agent`。
  GitHub 憑證只放在推送者自己的電腦上，不放在 Spark：先從 Spark `git fetch` 到自己的電腦，再推上 GitHub。
  `logs/` 沒有進 git，實驗紀錄只存在 Spark 上。
- **共用帳號**：Spark 上所有人都用 `hsnl`，權限相同。2026-10-20 晚上有組員在 `~/edge-agent-demo`（`demo-infra` 分支）
  工作，而且**重建了測試容器，把前端設定改成從 `~/edge-agent-demo/victim/conf.d` 掛載**。
  要在 `~/edge-agent` 跑演練前，先協調時段，再用 `.venv/bin/python scripts/faults.py setup` 重建回本分支的設定
  （這會讓 demo 那邊的掛載失效）。**兩邊不能同時跑演練**：容器、沙盒、工具 API、shim、推論服務都只有一套。
- **改完程式一定要重啟常駐服務**：`ops_api.py`、`oai_shim.py` 都是常駐程式，啟動時就把程式碼載入記憶體。
  2026-10-19 17:29 ～ 10-20 15:53 工具 API 一直跑著舊版，導致 `d341f6f` 的 agent 修改有一天沒有真的被測到。
  `scripts/ab-speed.sh` 已改成每場演練前都重啟兩者；`drill-all.sh` **沒有**，用它之前要自己重啟。
- **記憶體沒有 swap，CPU 和 GPU 共用 119 GiB**：推論服務約占 60 GB。不要再載入大模型或大量資料；
  10-17 曾因此整台卡死，要實體重開機。需要時用 `scripts/moe-routing-stats.py` 裡的記憶體保護寫法。
- **磁碟 96%（剩約 42 GB）**：`logs/prune-calib/nano-nvfp4-e112`（17 GB）和 HF cache 裡的 BF16 Nano（59 GB）是最大的兩塊。
  **不要** `docker system prune`，**不要**刪 HF cache 或映像檔（10-15 有人清磁碟時把 TRT 映像刪掉，重新下載了一次）。
- **沙盒備份**：10-18 沙盒重建前的完整備份在外接硬碟 `/media/hsnl/TRANSCEND/edge-agent-sandbox-backup-20261018/`
  （`sandbox.tar` 內含 769 份對話紀錄），本機另有 `logs/sandbox-backup-20261018/`。

## 1. 環境怎麼啟動

| 服務 | 埠 | 啟動 | 說明 |
|---|---|---|---|
| TensorRT-LLM（Nano NVFP4） | 8000 | `HOST=0.0.0.0 setsid nohup scripts/serve-trt.sh > logs/trt-serve.log 2>&1 &` | 設定 `trt/nano.yaml`。**不要用 `trt/nano-ngram.yaml`**（輸出會壞，見 §3.6） |
| oai_shim | 8001 | `bash scripts/shim-env.sh "SHIM_THINK=1"` | 預設開思考、保護網常駐；`SHIM_ADAPTIVE=1` 開分情況思考 |
| ops_api（MCP 工具） | 8790 | `setsid nohup .venv/bin/python scripts/ops_api.py > logs/ops-api.out 2>&1 &`，pid 寫入 `logs/ops-api.pid` | 環境變數 `KB_RETRIEVAL=v3`（預設）/`v4` |
| 測試容器 | 8880 | `.venv/bin/python scripts/faults.py setup` / `reset` | `edge-victim`（前端）、`edge-victim-app`（上游）、`edge-victim-cache` |
| NemoClaw 沙盒 | — | `systemctl --user status nemoclaw-openshell-gateway` | 壞掉時見 §5 |

沙盒從頭建：`COMPATIBLE_API_KEY=none scripts/bootstrap-spark.sh nemoclaw`（`4fdb15c` 修好了原本會無限遞迴的 bug）。

## 2. 怎麼跑實驗

- 單場：`bash scripts/nemoclaw-drill.sh <case>`（`TRIAGE=1` 時系統先收集資料放進告警）
- 多場：`scripts/drill-all.sh <case> <case> ...` → `logs/drills-<時間>.log`，結尾出現 `ALL_DONE`
- 兩組對照、每組多輪：`setsid nohup bash scripts/ab-speed.sh &`（18 題 × 3 輪 × 2 組 ≈ 7 小時；組別寫在檔案開頭）
- 單題對照：`scripts/ab-drill.sh <case> <每組次數> "<A 組 env>" "<B 組 env>"`
- 評分：`.venv/bin/python scripts/score.py logs/<log> [--json out.json] [--shim logs/oai-shim.log]`
- 故障清單（含正確修法、根因 regex、tier）：`scripts/faults.py` 的 `CASES`。
  共 28 題：`seen` 9 題加 `unseen` 1 題（inode 用盡），合稱「見過的 10 題」；四批盲測 `blind`、`blind2` 各 5 題，`blind3`、`blind4` 各 4 題。
- **每題只跑 1 次的結果不能信**：同設定重跑一次（A/A）就差了 1 場，個別題目會互換成敗。要比較請每題至少 3 次。
- 推論速度：`.venv/bin/python scripts/decode-bench.py`，會同時檢查輸出內容，避免像 n-gram 那樣被假速度騙。

## 3. 這個分支做了什麼（照時間）

### 3.1 知識庫檢索（v2/v3/v4）
- v1 的 `matched` 判斷 21 次錯 20 次；v3 改成以案例自己的遙測建索引、單字邊界 IDF、卡片需「完整症狀片語」命中（門檻 8）。
  在保留的評測集上，從「害多於助」（壞 50、修 16）變成「助多於害」（壞 16、修 53）。`docs/kb-retrieval-v2.md`、`docs/kb-eval-v3.md`。
- **知識庫本身只教診斷**：1,557 個公開案例（Kubernetes 混沌實驗為主）沒有任何修法；17 張舊卡片用的是 kubectl。
- v4（`7498b79`）：14 張「本環境工具版」修復卡（每張對應一個公開故障家族，1,550/1,557 個案例連到卡片），
  加上從演練萃取的 17 筆驗證過的修法（`kb/cards/`、`scripts/kb-cards.py`）。
- **結論：v4 沒有可量測的效果**（第三、四批盲測合計 v3 11/24、v4 9/24），**預設維持 v3**。
  卡片只在 AI 剛好搜尋、而且用詞命中時才會出現，多數場次根本沒送到。`docs/kb-repair-cards.md`。

### 3.2 安全規則（審核與工具層）
- 拒絕訊息一律寫明「誰拒絕的、下一步該呼叫什麼」；AI 被拒後成功補救的比例從 50% 升到 80%。
- `cleanup_disk` 只能刪白名單內的檔案（之前曾刪掉演練紀錄兩次）。
- 重建條件放寬：上游 restart 後仍有任何 HTTP 錯誤，就可以重建（`a17bcd9`）；被拒絕的 start 不算 restart（`c3f758d`）。
- 前端可以重建的條件：映像檔裡的檔案（`conf.d` 以外）被刪或被改；`conf.d` 是主機檔案，重建沒用（`d341f6f`）。
- 每個容器的檔案層和正常狀態比對（`faults.layer_changes`），`docker_logs` 開頭會標明是哪個容器、改了哪些檔案。
- 上游沒在跑時，禁止重啟健康的前端（之前因此越修越壞 2 次）；健康檢查不跟隨跳轉，301 直接回報。

### 3.3 保護網：系統實測健康狀態（`189de86`）
- shim 在 AI 最後一則回覆後面，附上系統自己量的健康狀態。
- AI 宣稱修好但實測沒好時，改成插入一次 `check_health`，讓它繼續處理（每場最多 2 次）。
- 關閉思考時，最終報告的謊報從 6 降到 0，修好的場次從 6/18 升到 9/18。開啟思考時保護網從未介入，沒有成本，是一層保證。

### 3.4 加速
- 時間分布：模型生成約 71%、讀 prompt 約 6%、其他約 22%。生成速度約 57 token/秒，讀 prompt 約 11,000 token/秒。
- 完全關閉思考：快 4～5 倍，但修好的場次從 11/18 掉到 6/18，而且有 6 次謊報 → **不採用**（`fa17f46`，也修掉了 `SHIM_THINK=0` 根本沒關掉思考的 bug）。
- **分情況開關思考 + 開場資料**（`fd7a79a`，各 54 場）：修好 43/54 vs 41/54；模型呼叫少 45%；
  成功場次的中位時間，見過的題目 56→38 秒、困難題目 208→137 秒；兩組都是 0 謊報、0 越修越壞。
  **已知缺陷**：拒絕訊息也含「Next:」，被拒後的那一輪也被關掉思考，但那一輪正好需要重新規劃。待修，見 §4。

### 3.5 模型剪枝（MoE 專家剪枝）
- 路由統計（`docs/moe-routing-stats.md`）：本任務用到的專家很穩定，而且跟一般任務不同（Jaccard 0.94 / 0.43），但不集中：保留 96/128 時覆蓋率約 92%。
- 每層 128→112 個專家的 NVFP4 剪枝版（`scripts/prune-experts.py`）：18.5→16.1 GiB，見過的題目＋第二批盲測 21/25，和原版相同（`docs/expert-pruning.md`）。
  **推論不會變快**（每個 token 仍然啟用 6 個專家）。成品在 `logs/prune-calib/nano-nvfp4-e112/`（未進 git）。

### 3.6 已排除的方向（不要重做）
- **n-gram 推測解碼**：TRT-LLM 1.3.0rc13 + Nano 的輸出會壞掉（只剩一個字），量到的 4 倍速度是假的（`trt/nano-ngram.yaml` 開頭有註解）。
- 前綴快取最多省 6%，而且混合架構不一定支援；rerank 只影響讀 prompt 的那 6%，對速度沒有幫助。

## 4. 待辦（依優先順序）
1. **分情況開關思考的缺陷**：`oai_shim.py` 的 `DECIDED` 判斷要排除 `refused:`、`denied:`、`review-rejected:` 開頭的工具結果；修完只要重跑 B 組 3 輪，A 組沿用 `ab-20261020-155259-A`。
2. **評分器太寬鬆**：「謊報」會掃整場文字，而且「front-end is healthy」「restored the config」也會被算進去；根因 regex 也太寬鬆（例如 sigkill 被說成 OOM 仍算對）。應該只看最後一份報告、只算整體宣稱。
3. **跟 `demo-infra` 協調共用測試環境**，並請組員把留在 `~/edge-agent` 的未追蹤檔案移回他的 worktree（13 個檔案，加上 `victim/board/`）。
4. **push**：解決 GitHub 權限問題。
5. **貼近真實場域**：研究整理見 2026-10-19 的對話，摘要如下。
   真實事故以變更/設定（Google 約 70%）、依賴服務（約 16%）、基礎設施和電力（27～45%）為主；我們的測試環境只有兩個 nginx 容器。
   建議順序：重開機/斷電後的復原（`feat/reboot-recovery`）→「最近改了什麼」與映像版本回滾 → 擴成三層架構（加資料庫或快取）→ 保護推論服務和沙盒本身。
   **每個方向開一個新分支**，並保留兩容器環境當回歸測試。
6. 盲測仍修不好的題目：`front-worker-connections`（0/6）、`upstream-config-crash`（B 組 1/3）、`upstream-redirect`（B 組 1/3）。

## 5. 踩過的坑
- 沙盒卡在 `Error` 狀態時，`start`、`stop`、`recover`、`rebuild --force` 都無效。
  做法：先備份 `/sandbox`（`docker cp <container>:/sandbox - > sandbox.tar`），再 `nemoclaw edge-agent destroy --yes --no-cleanup-gateway`，
  最後 `COMPATIBLE_API_KEY=none scripts/bootstrap-spark.sh nemoclaw`。
- `pgrep -f` 和 `pkill -f` 會比對到自己的 ssh 指令列，所以要用 pid 檔，或用 `[x]` 這種寫法避開自己。
- 透過 ssh 背景執行時，要用 `setsid nohup ... < /dev/null &`，否則連線一斷，程式就被一起結束。
- 等待用的迴圈一定要有結束條件。曾有 6 個迴圈等了 6 天，每 30～60 秒連線一次。
- 每場演練前要等工具 API 安靜 60 秒（quiesce）。被逾時中止的演練，其背景 agent 可能還在呼叫工具；逾時後要重啟沙盒 gateway。

## 6. 主要紀錄檔
| 實驗 | 紀錄 |
|---|---|
| 開場資料 + 分情況開關思考，3 輪 | `logs/ab-20261020-155259-{A,B}.log` |
| 開啟/關閉思考，以及保護網 | `logs/ab-20261019-195910-A.log`、`ab-20261019-231122-B.log`、`ab-20261020-132501-B.log`、`ab-20261020-141410-B.log` |
| 第四批盲測（v3/v4） | `logs/ab-20261019-144744-{A,B}.log` |
| 第三批盲測（修規則前/後） | `logs/ab-20261018-171405-*`、`ab-20261018-203657-*`、`ab-20261019-132608-*` |
| 剪枝版 25 場 | `logs/drills-20261018-145528.log` |
| 回歸測試（見過的 10 題） | `logs/drills-20261017-202633.log`（基準）、`drills-20261019-173903.log` |
| 凍結紀錄 | `logs/blind4-freeze-20261019.txt`、`logs/kb-cards-frozen-*.sha256` |

各實驗的完整數字和分析都在 `docs/agent-design.md`、`docs/kb-repair-cards.md`、`docs/expert-pruning.md`。
