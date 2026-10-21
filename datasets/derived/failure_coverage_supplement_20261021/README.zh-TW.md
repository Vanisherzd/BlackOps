# DGX 困難故障資料補齊（2026-10-21）

本批依 DGX 原有 55 份實驗紀錄整理，沿用專案原生 JSON 修復卡與 cases_train.jsonl 格式。

- 原有 failed_drills_20261021 保留 24 筆。本目錄新增 87 筆，合計 111 筆困難故障紀錄，涵蓋 10 類問題。
- 新增 4 張修復卡、補強 2 張既有卡；新增 4 份操作手冊、補強 2 份。
- 每筆保留來源紀錄、雜湊、實驗時間、觀察結果、診斷工具、修復順序、回復方式及驗證指令。人員才能執行的指令另行標示；不繞過既有工具與審查條件。
- 補入首頁刪除／權限錯誤、mime.types 缺失、SIGSTOP、301／429 規則、空白組態，以及先前漏收的 PID 限制、worker_connections 與後端組態錯誤紀錄。

## RAG 如何使用

datasets/raw 保存原始公開資料，datasets/derived 保存本機實驗整理結果。執行中的 RAG 不會遞迴掃描 datasets；本批已合併至 kb/artifacts/corpus_v3.jsonl 與 corpus_v4.jsonl，修復卡位於 kb/cards，操作手冊位於 runbooks。runbooks 的 logs/rag-index.npz 已重建。

DGX 的 API 使用 KB_RETRIEVAL=v4。部署到其他機器時，須保留此環境設定，才能使用原有 Docker 卡與經驗紀錄較完整的 v4 語料；程式預設值仍為 v3。更新後必須重建操作手冊索引並重新啟動 API，清除舊的記憶體語料快取，詳見 ../../../docs/dgx-failure-knowledge-20261021.md。

## 驗證狀態與限制

依使用者要求停止後續測試，只完成資料補齊與套用。歷史 FAIL/TIMEOUT 不改成 PASS，TIMEOUT 也不當作已診斷；本批沒有執行完整的模型自主修復重測。已產生的部分實驗紀錄保留在 DGX 的 replay-*，未提交 Git，不當作完整通過的證明。

原有 NemoClaw/OpenShell 沙箱仍為 Error；另觀察到修復審查逾時。這些執行問題不靠資料集即可保證解決，相關證據及官方參考保留在 fault-reference-audit.md。未套用本機草擬的審查程式修改，未升級或重建沙箱。

原始資料集、模型及歷史紀錄保留在 DGX；只在已授權的 edge-cache 實驗暫存區逐一清理指定路徑。Git 僅提交精簡案例、修復卡、操作手冊及既有文字語料；原始 benchmark、模型、完整日誌、實驗輸出與備份不提交。此目錄保留來源及重建依據。
