# 企畫書 Dataset 功能完成稽核

範圍只含 dataset／Knowledge Layer／日誌資訊量治理；不把即時監控 Agent、Nemotron、OpenShell、審批執行算入本次完成條件。

| 企畫書需求 | 實作 | 驗證證據 | 結果 |
|---|---|---|---|
| Disk Full、CrashLoopBackOff、DNS | 正規化場景、案例搜尋、12 張解法卡 | `manifest.json` 場景計數；三場景 retrieval tests | 通過 |
| 歷史事件 | 2,027 案轉成 JSONL；1,644 train 案進 SQLite | `cases_*.jsonl`、DB `case=1644` | 通過 |
| 內部 SOP | 解法卡含 R/W/D、checks、remediation、rollback、verify | `solutions.json`、solution retrieval tests | 通過 |
| 拓撲圖／服務依賴 | RCA100 topology 去重成 7 份 dependency documents | `topologies.jsonl`、topology retrieval test | 通過 |
| 原始多模態資料 | JSON、JSONL、CSV、TSV、Parquet、tar.gz 延遲載入 | `dataset_validation.json`，5 個來源 adapter 皆 pass | 通過 |
| 規則式過濾 | health／heartbeat 等無資訊行過濾；keyword 命中不刪 | compression unit test；跨來源驗證 | 通過 |
| 去重 | template fingerprint；保留 count 與首末時間 | compression unit test；`duplicates_collapsed` | 通過 |
| 時窗聚合 | incident window 前後 margin 過濾 | `compression.py::_in_window`；prepare-eval 實跑 | 通過 |
| 關鍵字錨定 | 依 hypothesis keywords 排序、優先保留 | `compress_events(... keywords=...)` | 通過 |
| 壓縮比實驗 | 1×、2×、4×、8× 產生 label-free inputs | `artifacts/validation/{disk,crashloop,dns}` | 通過 |
| 正確率曲線 | predictions scorer 輸出 accuracy/tokens/latency/energy CSV | `compression_curve_smoke.csv`；scoring unit test | 通過（scorer） |
| 工作點選擇 | 正確率距最佳 1 個百分點內選 tokens 最少者 | `.summary.json` | 通過 |
| 答案隔離 | eval opaque ID；input 與 ground truth 實體分檔；eval 不進 DB | key leak 0；DB forbidden row 0 | 通過 |
| 氣隙離線 | SQLite FTS5；執行期不需網路／雲 API | wheel 安裝後 CLI/API tests | 通過 |
| 來源可重現 | 四資料集 commit、核心 artifact SHA256 | `manifest.json` | 通過 |

## 邊界

- `compression_curve_smoke.csv` 使用合成 prediction，只驗證評分功能；不能宣稱真實 Nemotron 診斷正確率。
- 真實曲線須由專案模型對 `prepare-eval` 輸出產生 predictions，再交 `blackout-kb score`。
- 即時 `kubectl`／Proxmox／Linux collector、Diagnoser、Planner、Safety Reviewer 屬 Agent 專案功能，不屬 dataset 包。
