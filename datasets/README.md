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
