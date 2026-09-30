# Diagnoser → Planner 知識合約

Diagnoser 呼叫 `search_incident_knowledge`。輸入必須包含：

- 平台、namespace、服務／資源
- 主要症狀與精確錯誤字串
- 最近一次 terminated reason／exit code
- 異常時窗
- 已執行的唯讀檢查

回傳資料只產生候選假設。不得視為當前環境事實。

Planner 輸出：

```json
{
  "root_cause": "...",
  "confidence": 0.0,
  "supporting_current_evidence": [],
  "contradicting_evidence": [],
  "missing_checks": [],
  "proposed_actions": [
    {"level": "R|W|D", "action": "...", "approval_required": true}
  ],
  "blast_radius": "...",
  "rollback": [],
  "verification": []
}
```

執行條件：

1. 根因須有當前環境證據，不得只引用歷史案例。
2. `confidence` 未達專案門檻時，只能追加 R 級檢查或升 Tier-2。
3. 所有 W、D 動作交 Safety Reviewer；D 強制人工核准。
4. 修復後必跑 verification；失敗立即停止並提出 rollback。
