# DGX 原始困難故障紀錄（2026-10-21）

保留 24 筆實際失敗或逾時案例：14 筆 FAIL、10 筆 TIMEOUT，涵蓋 PID 限制、前端 worker_connections 與後端組態錯誤。每筆包含診斷片段、原始紀錄雜湊、明確工具呼叫、修復順序、回復限制及驗證方式。

來源採專案原生 cases_train.jsonl 格式；修復卡位於 `kb/cards/failed_drill_cards.json`，已編入 `kb/artifacts/corpus_v3.jsonl` 與 `corpus_v4.jsonl`。後續 [補充資料](../failure_coverage_supplement_20261021/README.zh-TW.md) 新增 87 筆，兩者合計 111 筆，來源依紀錄雜湊、實驗時間及故障種類去重。

歷史 FAIL/TIMEOUT 不會改成修復成功，缺少最後健康檢查的紀錄仍標記為未知。依使用者要求未繼續完整故障重測；資料補齊不保證模型能自主解決所有案例。

完整實驗日誌、安裝報表、驗證輸出與備份保留在 DGX，未提交 Git。部署使用已更新的語料與操作手冊，並設定 `KB_RETRIEVAL=v4`，詳見 [部署說明](../../../docs/dgx-failure-knowledge-20261021.md)。
