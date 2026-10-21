# Bolt 與 Nano 在困難題目上的比較（2026-10-21）

作者：vanli。一句話結論：**同樣 8 題困難題目各跑 3 次，Bolt 自主修好 18/24，Nano 12/24；根因說對的，Bolt 18/24，Nano 6/24。**
Bolt 修好時是真的看懂了問題；Nano 修好的場次有一半說錯原因，是靠「先重啟、不行就重建」的通用套路碰對的。

## 1. 實驗設定

| | Nano | Bolt |
|---|---|---|
| 模型 / 引擎 | Nemotron-3-Nano-30B-A3B-NVFP4 / TensorRT-LLM 1.3.0rc13 | Nemotron-3.5-Bolt-74B-A9B / vLLM（`blackbox-vllm-bolt:77154ca`，`scripts/serve-vllm.sh`） |
| 程式碼 | `kb-retrieval-v2`（`fd7a79a` 時期） | `integrate-vllm@b14d401` |
| 紀錄 | `logs/ab-20261020-155259-A.log`（A 組） | `logs/bolt-hard-20261021-101005-B.log` |

- **相同的部分**：DGX Spark、NemoClaw 沙盒路徑；開啟思考、shim 的健康保護網開著；不開分情況思考、不開開場資料；知識庫 v3；每場上限 10 分鐘；每場演練前都重啟工具 API 和 shim。
- **題目**：第三、四批盲測的 8 題，每題 3 輪。
- **程式碼的差異**：整合版在 `agent.py` 多了審批和 Tier 的掛鉤，這次都關閉；另外 `runbooks/upstream-502.md` 改了一行、`nemoclaw/workspace/AGENTS.md` 改了一行，以及前端多掛載一個看板目錄。判斷影響很小，但兩邊並非完全相同。
- 重跑方式：`scripts/ab-bolt-hard.sh --check` 檢查環境，再用 `setsid nohup bash scripts/ab-bolt-hard.sh &` 開跑（約 2.5 小時）。

## 2. 結果

逐場結果：`Y` 自主修好、`.` 失敗、`T` 逾時（10 分鐘）。

| 題目 | Nano | Bolt | Nano 成功場次的中位時間 | Bolt 成功場次的中位時間 |
|---|---|---|---|---|
| upstream-perm-denied | YYY | YYY | 344 s | 112 s |
| upstream-sigstop | YY. | YYY | 270 s | 249 s |
| upstream-429 | YYY | YYY | 57 s | 144 s |
| front-mime-missing | .Y. | YYY | 97 s | 132 s |
| upstream-config-crash | T.. | YYT | — | 365 s |
| upstream-pids-limit | YYY | TTT | 160 s | — |
| front-worker-connections | ... | YTT | — | 485 s |
| upstream-redirect | ... | YYY | — | 191 s |

| | Nano | Bolt |
|---|---|---|
| 自主修好 | 12/24 | **18/24** |
| 根因說對（評分器 regex） | 6/24 | **18/24** |
| 謊報 / 越修越壞 | 0 / 0 | 0 / 0 |
| 逾時 | 1 | 6 |
| 成功場次的中位時間 | 208 s | **149 s** |
| 模型呼叫次數 / 生成 token | 331 / 32.2 萬 | **153 / 6.6 萬** |
| 每次呼叫平均生成 | 972 | **431** |
| 24 場總時間（含逾時） | 7,675 s | 9,195 s |

差距 6 場，大於同設定重跑的波動（A/A 實驗差 1～2 場）。

## 3. 為什麼 Bolt 比較好

**根因。** Bolt 每一場修好的都說對了原因，而且會引用證據：
- 「`/etc/nginx/conf.d/default.conf` 被改成 `return 429;`」
- 「worker 收到 SIGCHLD 後停止處理請求，容器卻顯示 Up」
- 「`nginx.conf` 被改成 `worker_connections 2;`」

Nano 修好的場次則常常說錯原因：
- 429 那題，3 次都說成「回傳 500」。
- pids 上限那題，3 次都說成「被移出網路」或「上游掛了」，其實是不能 fork。
- worker 被凍結那題，說成「失去網路連線」。

**證據的使用。** Bolt 的報告常直接引用 `docker_logs` 開頭的「容器內檔案相對正常狀態的變更」（`d341f6f`），例如 index.html 的權限、被刪掉的 mime.types、被改寫的設定檔。所以這項功能對 Bolt 有實際作用。

**方向。** Nano 常走錯路：在跟網路無關的題目上做了 9 次網路修復；上游出問題，卻去改前端的設定或重啟前端。Bolt 第一步通常就要求重建出問題的那個容器，這正是這些題目的正確修法。

**效率。** Bolt 每次呼叫只生成 Nano 的一半不到，呼叫次數也少一半以上。所以它的生成速度雖然比較慢（約 33 vs 57 token/秒），修好的場次反而比較快。

## 4. Bolt 的弱點

- **被「重建前要先 restart」這條規則擋了 28 次。** Bolt 一開始就看出要重建，被擋之後才補做 restart，每次多花一輪，約 20～30 秒。Nano 被擋了 19 次。
- **6 次逾時**：pids 上限 3 次、前端 nginx.conf 2 次、上游設定錯誤 1 次。
  - 從 shim 的紀錄看，這些場次 Bolt 一直在工作（每場呼叫模型 10～27 次），是時限到了被中止，不是卡住。
  - 被中止的場次沒有留下工具呼叫紀錄，因為外層的 `timeout 600` 比演練腳本自己的 15 分鐘上限早結束。所以還看不出它卡在哪一步。
  - 時限對兩邊相同，比較是公平的。但 Bolt 每一步比較慢，10 分鐘對它比較不利。
- **pids 上限 0/3**。Nano 雖然 3/3，但根因都說錯：它是靠「啟動失敗就重建」碰對的。Bolt 試著搞懂為什麼不能 fork，結果在時限內沒有完成。

## 5. 建議

1. **以 Bolt 為主**，跟團隊的決定一致。簡報可以這樣寫：「困難題目自主修復 18/24（Nano 12/24），根因正確 18/24（Nano 6/24）；推論較慢，但修好的場次反而較快（149 vs 208 秒）。」
2. **放寬一條規則**：工具已經回報「容器內的檔案相對正常狀態有變更」時，允許直接 `recreate_stack replace=true`，不必先 restart，因為 restart 會保留被改過的檔案。這樣可以省掉 Bolt 大部分的 28 次被擋。
3. **重跑逾時的題目**：時限放寬到 15 分鐘，並保留工具呼叫紀錄（例如外層 timeout 設成 950 秒），看清楚 pids 上限和前端 nginx.conf 卡在哪裡。
4. Nano 仍然適合當退路，例如 Thor 64 GB 機型或 Bolt 跑不起來的時候。它在簡單題目上表現相同，速度更快；但遇到通用套路修不好的故障時會吃虧。
