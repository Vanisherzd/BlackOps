# Blackout Ops portable RAG schema

`artifacts/rag_documents.jsonl` uses one JSON object per retrievable chunk:

```json
{
  "id": "stable-document-id",
  "text": "title plus evidence or runbook content",
  "metadata": {
    "schema": "blackout-ops-rag/v1",
    "doc_type": "case|solution|topology",
    "scenario": "disk_full|crashloop|dns|other",
    "platform": "linux|kubernetes",
    "title": "human-readable title",
    "tags": "space-separated retrieval tags",
    "source": "dataset or curated runbook source",
    "split": "train|knowledge",
    "risk_tier": 2
  }
}
```

Rules:

- `text` is ready for embedding or lexical indexing.
- metadata stays portable; no Windows drive, absolute host path, or raw telemetry locator.
- only `train` and curated `knowledge` documents enter RAG.
- dev/eval answers stay outside corpus.
- solution chunks contain Linux/Kubernetes checks, remediation, rollback, verification, and R/W/D safety levels.
