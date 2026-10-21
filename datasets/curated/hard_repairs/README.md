# 少見且曾修復失敗的 RAG 修復知識

這批只補專案曾經自主修復失敗的難例，不擴增普通容器停止、DNS、OOM、磁碟滿或已經 6/6 修好的 PID 限制案例。
每筆包含必要證據、帶條件的工具呼叫、回復限制、驗證、升級條件及官方來源。它是自行編寫的修復知識，
不是新增下載的公開 benchmark，也不是新蒐集的真實事故。

| 知識 | 曾經失敗的原因 | 對應既有工具 |
|---|---|---|
| 上游自己的設定語法錯，卻修到前端同名路徑 | 沒有分清 log 的發出容器與設定檔歸屬，甚至把仍正常的前端停掉 | `docker_logs` → 上游 `docker_restart` → `check_health` → 條件成立才 `recreate_stack` |
| 前端主設定 `worker_connections` 被改壞 | `nginx -t` 可能仍通過；還原站台設定無效；舊 guard 不允許修復主設定層 | `docker_logs`、`docker_exec nginx -T` → 重啟驗證 → 有實測 layer 變更才重建前端 |
| 映像內 include 檔遺失 | `restore_config` 只能還原掛載的站台設定，無法補回映像內檔案 | `docker_logs` 確認檔案及 layer → 重啟驗證 → 條件式重建前端 |
| 上游檔案權限損壞，重啟仍 403 | 與驗證失敗、前端 policy、index 遺失混淆；重啟不會還原可寫層權限 | 上游 `docker_logs`、`docker_exec ls -ld` → 重啟驗證 → 條件式重建上游 |

失敗數據來自 [agent-design.md](../../../docs/agent-design.md) 與
[kb-repair-cards.md](../../../docs/kb-repair-cards.md)。那些是 **Nano 與當時程式版本** 的紀錄。
部分工具與 guard 後來已修正；不能把舊版失敗率當成目前 Bolt 的能力或這批 RAG 的改善幅度。

## 模型如何取得知識

```text
告警／當前 log（不帶答案）
  → 模型呼叫 search_incident_knowledge，或證據工具觸發既有 RAG 查詢
  → BM25 從原知識庫與這 4 張卡中檢索，沿用既有 phrase confidence 門檻
  → 命中後才回傳修復假設、必要證據與條件式 tool + args
  → 模型讀取當前證據，再呼叫既有修復工具
  → 原有工具條件、reviewer 與最終健康驗證照常執行
```

權重、system prompt 與工具權限均未改動。方法存在 RAG，不預先塞入事故初始 prompt。
預訓練模型是否早就知道某個方法無法從程式保證；要比較的是「相同模型與設定，有／無這批 RAG 的差異」。
檢索只是提出候選方法，不會自動執行 JSON 裡的工具。

預設的 `KB_RETRIEVAL=v3` 與 `v4` 都會在原 corpus 上加入這 4 筆文件；不重建或覆寫原 corpus。
`v1` 保留為原有消融基準。設定 `OPS_HARD_REPAIR_KNOWLEDGE=0` 可在新啟動的 agent/API 程序中關閉這批補充知識，
既有資料與工具仍保留。API 的知識庫會快取，切換設定後需重新啟動該測試 API 才會生效。

## 知識與測試分離

| 檔案 | 使用者 | 是否進 runtime RAG |
|---|---|---|
| `knowledge.json` | 檢索器 | 是，唯一 runtime 入口 |
| `eval_inputs.jsonl` | 測試執行器；模型只可看到其中的 `observation` | 否 |
| `eval_ground_truth.jsonl` | 評分程式，含預期卡片／工具／目標 | 否 |
| `negative_queries.json` | 誤檢測試 | 否 |
| `manifest.json` | 資料來源與量測範圍紀錄 | 否 |

8 筆測試輸入是人工編寫的不同路徑／措辭變體，不是新的真實事故，也不是未見過的故障家族。
**這不是嚴格凍結後、獨立作者產生的盲測集**；目前只能驗證資料隔離、檢索與工具介面。
修復卡有作者編寫的診斷與工具路徑；評分答案不進索引，測試輸入的完整文字也不進索引。

## 離線驗證

在 repo 根目錄執行：

```bash
python scripts/eval-hard-repairs.py
python -m unittest tests.agent.test_repair_knowledge tests.agent.test_host_safety -v
```

第一個命令只用 Python 標準庫與現有 BM25；不呼叫模型、不注入故障、不執行修復或刪除資料。
它會同時檢查 v3/v4、8 筆變體、10 筆不應誤命中此批卡片的問題，以及每個工具呼叫的名稱、必要參數、型別與目標。
`8/8` 指檢索命中，不是模型修復成功率。

2026-10-21 本次離線結果：v3 與 v4 各 8/8 命中預期修復卡，10 筆反例均未把這批難例卡選為最佳修法，
34 個工具呼叫範例通過既有介面檢查。另有 49 項明確選定的單元／mock 測試通過，92 個 Python 檔案通過語法檢查。
含遞迴暫存目錄清理的既有 benchmark／KB 測試模組依工作區禁止批量刪除規則排除，沒有執行。

後續 Bolt A/B 應先凍結本批知識與程式雜湊，再另行編寫新故障變體，答案另存於 evaluator；
兩組使用同一模型、thinking、工具與 guard，只切換 `OPS_HARD_REPAIR_KNOWLEDGE=0/1`。
模型起始訊息只含告警及可觀測證據，不含案例 ID、根因、推薦工具或修復步驟。
記錄是否主動檢索、取到哪張卡、工具參數、修復成功、誤修、耗時與最終健康；
避免把這次離線測試宣稱為 Bolt 已改善。DGX 正在演練時不自動執行新的故障注入或重啟服務。

## 官方參考

修復路徑是把官方機制對應到此專案的既有工具，並非官方提供這些工具名稱：

- [Docker storage](https://docs.docker.com/engine/storage/)：可寫層與外部掛載的生命週期。
- [Docker diff](https://docs.docker.com/reference/cli/docker/container/diff/)：檔案新增、變更及刪除的證據。
- [nginx command-line parameters](https://nginx.org/en/docs/switches.html)：`-t`／`-T` 的檢查範圍。
- [nginx core configuration](https://nginx.org/en/docs/ngx_core_module.html)：worker 連線數與資源限制。
- [nginx HTTP core](https://nginx.org/en/docs/http/ngx_http_core_module.html)：靜態檔案與路徑處理。

知識僅適用於專案定義的無狀態 victim 容器。重建不能回復已丟棄的可寫層資料；
外部掛載、正式有狀態服務、或無法證明檔案歸屬時，卡片要求補證據或升級處理。
