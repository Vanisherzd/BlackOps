# P0 資料使用方式

## 建議角色

- **AgenticOpsEval**：主要診斷證據資料。優先做 telemetry 壓縮、跨模態檢索、根因與理由評分。
- **Cloud-OpsBench**：主要 agentic RCA 資料。用 `tool_cache.json` 做離線工具重播；用 milestone 評估是否查到正確證據。
- **RCAEval**：主要 Disk Full 類擴增資料。用 120 個 `_disk_` 案測試跨服務、跨系統泛化。
- **ITBench-Lite Scenario-12/105**：主要 CrashLoopBackOff 端到端測試。含 alerts、K8s objects/events、metrics、logs、traces。

## 防止答案洩漏

每個案例拆成三區，案例 ID 不可跨區重複：

```text
rag_knowledge/       只放訓練案例的 SOP、證據片段、已核准專家路徑
eval_inputs/         只放保留案例的告警、logs、metrics、traces、K8s state
eval_ground_truth/   放保留案例答案；評分程式可讀，RAG/agent 不可讀
```

評估時隔離以下欄位／路徑：

- AgenticOpsEval：`groundtruth.jsonl`、`RCA100/answer_key/`
- Cloud-OpsBench：`metadata.json` 的 `result`、`golden-trajectory/`、`process-label/`
- RCAEval：`cases.parquet` 的 root-cause 欄位、目錄名中的 service/fault、`inject_time.txt`
- ITBench-Lite：`ground_truth.yaml`

## 第一輪實驗

1. 先做場景分層切分：disk、crashloop、dns。
2. 每類保留至少 20% 案例，只當盲測。
3. 比較原始 telemetry 與壓縮摘要的 Token 數、RCA 準確率、證據命中率。
4. 用 Cloud-OpsBench milestone 與 RCA100 checkpoint 評估診斷過程，不只看最後答案。
5. 修復動作先只產生 plan；真實寫入仍走企畫書的 Human Gate。

## 授權限制

AgenticOpsEval 兩子集含 `NC` 限制；可研究、競賽、非商業原型，商業再利用需另行確認。RCA100 還要求衍生分享採相同授權。其他三組為 MIT 或 Apache-2.0。
