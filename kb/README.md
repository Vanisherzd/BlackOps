# Blackout Ops Linux RAG Knowledge Pack

Linux／Jetson／DGX 可離線使用的案例庫、Linux/Kubernetes 解法庫、服務依賴圖及 telemetry 壓縮工具。Runtime 只需 Python 3.10+ 與標準庫 SQLite FTS5；不依賴 PowerShell、CUDA、雲端 API 或 20+ GB 原始 dataset。

## 企畫書對應

- 部署：Jetson AGX Thor；同一包亦可在 DGX／Ubuntu／x86_64／ARM64 執行。
- Knowledge Layer：歷史事件、內部 SOP、拓撲、服務依賴。
- 優先場景：Disk Full、CrashLoopBackOff、DNS。
- Linux 延伸：systemd restart loop、host OOM、resolver、TLS 憑證、port listener。
- RAG 格式：`id + text + metadata` JSONL；可匯入 vector store，亦附 SQLite FTS5 離線索引。
- 資訊量治理：規則過濾、去重、時窗、關鍵字錨定、1×／2×／4×／8×。
- 安全：eval 答案隔離；解法 `plan_only`；低相似度回傳 `insufficient`。

## Linux 離線安裝

使用已打包 tar.gz：

```bash
tar -xzf blackout-ops-linux-rag-0.3.0.tar.gz
cd blackout-ops-linux-rag-0.3.0
bash linux/install.sh
linux/blackout-kb doctor
```

使用完整 repo：

```bash
cd ~/edge-agent/kb
bash linux/install.sh
bash linux/verify.sh
```

wheel 自帶 DB 與 RAG JSONL。安裝後可在任意工作目錄零參數使用：

```bash
python3 -m blackout_ops_kb stats
python3 -m blackout_ops_kb context --incident incident.json
```

## Agent 查詢

事件 JSON：

```json
{
  "platform": "linux",
  "resource": "checkout-api.service",
  "status": "failed; restart counter increasing",
  "exit_status": 203,
  "log": "Start request repeated too quickly"
}
```

檔案查詢：

```bash
linux/blackout-kb context --incident examples/incident_linux_systemd.json
```

stdin 查詢：

```bash
printf '%s\n' '{"platform":"linux","log":"x509 certificate has expired"}' \
  | linux/blackout-kb context --incident -
```

輸出重點：

```text
knowledge_match.status   = matched | insufficient
cases                    = 相似歷史事故
solutions                = 通過相似度門檻的候選 SOP
weak_solution_candidates = 不可直接拿來修復的弱候選
topologies               = 相關服務依賴
policy.mode               = plan_only
```

`matched` 仍只是候選。Agent 必須用當前唯讀證據確認根因。`insufficient` 時不得修復，只能追加 R 級檢查或升級人工處理。

## Python RAG API

```python
from blackout_ops_kb import KnowledgeBase

kb = KnowledgeBase()  # uses DB packaged in wheel
bundle = kb.retrieve({
    "platform": "linux",
    "resource": "checkout-api.service",
    "status": "failed",
    "log": "Start request repeated too quickly",
})

if bundle["knowledge_match"]["status"] == "matched":
    print(bundle["cases"])
    print(bundle["solutions"])
else:
    print("No strong match; collect more read-only evidence")
```

現有自建 LangChain agent 可使用：

```python
from blackout_ops_kb.integration import create_langchain_tool

knowledge_tool = create_langchain_tool()  # add to Diagnoser tools
```

## NemoClaw / LangChain Deep Agents

bundle 內含 self-contained skill：

```bash
nemo-deepagents <sandbox> skill install integrations/nemoclaw/blackout-ops-rag
nemo-deepagents <sandbox> skill list
nemo-deepagents launch <sandbox>
```

skill 只讀本地 DB，無外部網路需求。詳見 [LINUX_DEPLOY.md](LINUX_DEPLOY.md) 與 `integrations/nemoclaw/blackout-ops-rag/SKILL.md`。

完成證據見 `docs/linux_rag_completion_audit.md`。

## RAG 文件格式

`artifacts/rag_documents.jsonl` 每行：

```json
{"id":"...","text":"...","metadata":{"schema":"blackout-ops-rag/v1","doc_type":"case|solution|topology","scenario":"...","platform":"linux|kubernetes"}}
```

匯出到任意 vector RAG：

```bash
linux/blackout-kb export-rag --out ./rag_documents.jsonl
```

完整 schema 見 `docs/rag_schema.md`。

## 從原始 dataset 重建

只在開發／評估機需要 `datasets/raw` 與 optional PyArrow：

```bash
python3 -m venv .build-venv
.build-venv/bin/python -m pip install -e '.[telemetry]'
.build-venv/bin/python -m blackout_ops_kb build \
  --datasets-root ../datasets/raw \
  --out-dir ./artifacts
.build-venv/bin/python scripts/build_linux_bundle.py
```

原始 dataset 不應複製到 Jetson／DGX runtime，也不得送入 Agent prompt。

## 壓縮評估

```bash
python3 -m blackout_ops_kb prepare-eval \
  --datasets-root ../datasets/raw \
  --ground-truth artifacts/eval_ground_truth.jsonl \
  --scenario crashloop --limit 10 --ratios '1,2,4,8' \
  --out-dir artifacts/experiment

python3 -m blackout_ops_kb score \
  --ground-truth artifacts/eval_ground_truth.jsonl \
  --predictions predictions.jsonl \
  --out artifacts/compression_curve.csv
```

`eval_ground_truth.jsonl` 僅供 evaluator；不得送入模型。

## Windows 開發機（非部署目標）

PowerShell 只供本機開發：

```powershell
$env:PYTHONPATH = ".\src"
py -X utf8 -m unittest discover -s .\tests -v
Remove-Item Env:PYTHONPATH
```
