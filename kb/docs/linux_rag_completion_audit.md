# Linux RAG 交付稽核

範圍：企畫書 Knowledge Layer／dataset；不宣稱已完成 Nemotron、Diagnoser、Planner、Safety Reviewer 或修復執行器。

| 要求 | 交付證據 | 結果 |
|---|---|---|
| Linux 可執行，不依賴 PowerShell | `linux/install.sh`、`linux/blackout-kb`；WSL Ubuntu Python 3.12 實裝 wheel | 通過 |
| DGX／Jetson 可攜 | wheel tag `py3-none-any`、無 native dependency、無 CUDA dependency；x86_64 Linux 實跑 | 通過；實機仍以 `doctor` 驗收 |
| RAG dataset 形式 | `rag_documents.jsonl`：`id`、`text`、portable metadata；schema `blackout-ops-rag/v1` | 通過 |
| 本地 Edge RAG | wheel 內建 SQLite FTS5 DB 與 JSONL；零參數 `KnowledgeBase()` | 通過 |
| Linux 修復知識 | Disk、systemd restart loop、host OOM、Linux DNS、TLS、port listener SOP | 通過 |
| Kubernetes 修復知識 | DiskPressure、CrashLoopBackOff、CoreDNS、Service／EndpointSlice、ImagePull | 通過 |
| 案例＋解法＋拓撲 | 1,644 train cases、17 solution SOP、7 topology；共 1,668 chunks | 通過 |
| 未知錯誤安全拒答 | `knowledge_match.status=insufficient`；強解法清單為空 | 通過 |
| NemoClaw Deep Agents 使用 | self-contained `blackout-ops-rag` skill，內含 wheel＋DB＋CLI wrapper | 通過（本地 skill wrapper） |
| 全離線 runtime | wheel 安裝用 `--no-index --no-deps`；查詢不連網 | 通過 |
| 不攜帶大型 raw dataset | Linux tar.gz 無 `datasets/raw` | 通過 |
| 不洩漏 eval 答案 | RAG 僅含 train／knowledge；tar.gz 無 eval／ground truth | 通過 |
| 跨來源資料處理 | Cloud-OpsBench、AIOps2025、RCA100、RCAEval、ITBench adapters | 5/5 通過 |
| 單元測試 | `tests/test_smoke.py` | 13/13 通過 |

## 實跑環境

- OS：Ubuntu on WSL，Linux x86_64。
- Python：3.12.3。
- SQLite：3.45.1，FTS5 啟用，integrity check `ok`。
- 驗證：離線 wheel install、package DB、RAG count、Linux incident retrieval、NemoClaw skill wrapper。

## 硬體邊界

ARM64 Jetson／DGX Spark 尚未取得實機；可攜性由 pure-Python `py3-none-any` wheel、標準庫依賴與無 CUDA/native extension 證明。上機仍須執行 `linux/blackout-kb doctor`，確認該映像的 Python SQLite 有 FTS5。
