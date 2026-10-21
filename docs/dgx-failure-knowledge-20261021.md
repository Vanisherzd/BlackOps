# DGX 困難故障知識部署

本次將 DGX 的 111 筆困難故障紀錄、7 張新增修復卡、2 張既有修復卡更新及 6 份新增或補強的操作手冊納入 Git。這些是供 RAG 使用的故障與修復假設，歷史 FAIL/TIMEOUT 不代表已解決，也不構成新的獨立評測集。

## 語料與檔案

- `datasets/derived/failed_drills_20261021/`：原有 24 筆困難案例及來源。
- `datasets/derived/failure_coverage_supplement_20261021/`：新增 87 筆及 21 類曾失敗故障的知識盤點。
- `kb/cards/failed_drill_cards.json`：PID 限制、worker_connections、後端組態錯誤。
- `kb/cards/failure_coverage_supplement.json`：mime.types 缺失、停止的工作程序、301/429 規則、空白站台組態。
- `kb/cards/docker_stack_cards.json`：補強後端 403/404 檔案與權限的診斷及修復。
- `kb/artifacts/corpus_v3.jsonl`、`corpus_v4.jsonl`：已合併上述來源的原生語料。此版本分別為 1,700 與 1,730 筆；v4 包含 1,685 筆案例、38 張解法卡及 7 筆拓樸資料。

原始 benchmark、模型權重、完整實驗日誌、執行報表、備份、`logs/rag-index.npz` 均留在本機資料磁碟，不隨本次提交上傳。案例只保留診斷片段、來源檔名與雜湊，原始完整紀錄不在 Git。

## 部署步驟

先同步此版本的 `kb/`、`runbooks/` 與專案依賴。模型及 BGE embedding 需另行準備；已有模型快取時可以離線重建手冊索引：

```sh
HF_HUB_OFFLINE=1 .venv/bin/python -B scripts/rag.py build
```

接著透過既有服務管理方式重新啟動 ops API，並在其環境中設定：

```sh
KB_RETRIEVAL=v4
```

這個設定必須放在實際服務的啟動環境。單獨在其他 shell 設定不會影響已啟動的程序；程式預設仍為 v3。API 會快取已開啟的語料，更新 JSONL 後也必須重新啟動。

RAG 直接讀取上述文字語料及操作手冊索引，不會自動遞迴掃描 `datasets/`。本次已提交編入後的語料，部署時不必重新下載大型原始 benchmark。若從零重建語料，須保留這 111 筆來源及 7 張新卡，不能僅以原始 benchmark 覆寫編入結果。

## 已知限制

依使用者要求，未繼續故障重測；沒有完整模型自主修復通過率。2026-10-21 的 DGX 上另有 NemoClaw/OpenShell 沙箱 `Error` 與審查逾時問題，本提交未修改其執行程式、升級或重建沙箱。參考資料及受限的恢復路徑見補充目錄的 `fault-reference-audit.md`。
