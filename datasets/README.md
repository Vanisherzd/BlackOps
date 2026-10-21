# Blackout Ops：第一優先資料集

依企畫書，只收集最直接支援下列展示與驗證的資料：

1. Disk Full／磁碟壓力
2. Kubernetes CrashLoopBackOff／Pod failure
3. DNS misconfiguration／DNS resolution failure
4. 多步驟根因分析、證據路徑、修復建議

## 已下載

| 順序 | 資料集 | 本機狀態 | 主要用途 | 授權 |
|---:|---|---|---|---|
| 1 | AgenticOpsEval（AIOps2025 + RCA100） | 完整；12.505 GiB；AIOps2025 官方 MD5 18/18 通過 | 三主場景、多模態遙測、證據標註、因果鏈 | CC BY-NC 4.0；CC BY-NC-SA 4.0 |
| 2 | Cloud-OpsBench | 完整；754 案、754 milestone、1,508 golden paths | 離線工具重播、多步診斷、K8s 狀態、專家路徑 | MIT |
| 3 | RCAEval | 完整；735 案、120 個 disk fault 案 | Disk Full 類故障、跨系統 RCA、metrics/logs/traces | MIT |
| 4 | ITBench-Lite | 精選完整下載 Scenario-12、Scenario-105；其餘只留索引與 ground truth | 兩個 CrashLoopBackOff 案、完整 SRE snapshot | Apache-2.0 |

原始資料位於 `raw/`；整理表位於 `manifests/`；使用規則位於 `docs/`。

## 少見修復失敗案例補充

另有 [hard_repairs](curated/hard_repairs/README.md)：4 種專案曾自主修復失敗的故障修復卡，
附官方來源、條件式工具呼叫、回復限制與驗證。這是人工整理的 RAG 知識，並非第五個下載的公開 benchmark。
8 筆合成測試變體與評分答案分開存放，不進 runtime 索引；普通啟停、DNS、OOM、磁碟滿不重複擴增。

## DGX 實際失敗紀錄補充

[failed_drills_20261021](derived/failed_drills_20261021/README.zh-TW.md) 保留 24 筆原始困難案例；
[failure_coverage_supplement_20261021](derived/failure_coverage_supplement_20261021/README.zh-TW.md) 再補 87 筆，合計 111 筆，涵蓋 10 類故障。
每筆包含來源雜湊、觀察結果、診斷與修復工具、回復限制及驗證方式；歷史 FAIL/TIMEOUT 維持原樣。

這批來源已編入 `kb/artifacts/corpus_v3.jsonl` 與 `corpus_v4.jsonl`，修復卡位於 `kb/cards/`，操作手冊位於 `runbooks/`。
執行時設定 `KB_RETRIEVAL=v4` 可使用完整 Docker 修復卡與既有經驗。`datasets/derived/` 本身不是自動掃描入口。
部署時需在目標機重建操作手冊索引，並重新啟動 API 清除舊語料快取，詳見 [部署說明](../docs/dgx-failure-knowledge-20261021.md)。

Git 僅保留精簡來源與既有文字語料；原始公開資料集、模型權重、完整日誌、實驗輸出及索引備份留在資料磁碟。

## 社群 Q&A 參考層（2026-10-22）

[stackexchange_ops_qa_20261022](derived/stackexchange_ops_qa_20261022/manifest.json)：Server Fault 與 Unix & Linux
Stack Exchange 上有已接受答案的 7,595 題（API 2.3，每個標籤取票數前 100 題，197 次呼叫），涵蓋 survey 指出 KB 為零的硬體／BMC、
UPS、L2/L3 網路、NTP／憑證、Proxmox／Ceph、K8s 控制面等 12 類。授權 CC BY-SA（每筆保留版本、題目與答案作者、連結）。
依類別分層抽 15%（1,139 題，種子 20261022）作 held-out，只給評測用；其餘 6,456 題編成 `kb/artifacts/community_v1.jsonl`，
是**獨立索引**，只在 `search_incident_knowledge` 沒有任何修復卡命中、且症狀不屬於本 stack 的 runbook 時附上，`knowledge_note`
與既有排名完全不變。`KB_COMMUNITY=0` 關閉。設計、審查、量測與限制見 [docs/kb-community-20261022.md](../docs/kb-community-20261022.md)。
`qa.jsonl`（完整題目與答案純文字）留在 DGX，Git 只收 manifest、held-out 與索引；`scripts/kb-community.py fetch` 可重抓。

## 目錄

```text
datasets/
├── raw/
│   ├── AgenticOpsEval/
│   ├── Cloud-OpsBench/
│   ├── RCAEval/
│   └── ITBench-Lite/
├── manifests/
│   ├── p0_datasets.csv
│   ├── p0_scenario_coverage.csv
│   └── download_status.json
└── docs/
    └── p0_rag_usage.md
```

## 重要

不要直接把 `groundtruth`、`answer_key`、`metadata.result`、`golden-trajectory` 與同一測試案例一起放入 RAG。否則模型只是撈答案，無法證明診斷能力。切分方法見 `docs/p0_rag_usage.md`。
