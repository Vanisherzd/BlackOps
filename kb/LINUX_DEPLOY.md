# Linux / DGX / Jetson deployment

This package is CPU-only retrieval. CUDA is not required. It runs beside local
Nemotron/NIM/TensorRT-LLM inference; it does not replace the model runtime.

## Offline install

```bash
tar -xzf blackout-ops-linux-rag-0.3.0.tar.gz
cd blackout-ops-linux-rag-0.3.0
bash linux/install.sh
linux/blackout-kb doctor
linux/blackout-kb context --incident examples/incident_linux_systemd.json
```

The wheel contains the SQLite FTS5 database and portable RAG JSONL. No raw
20+ GB benchmark download is needed at runtime.

## Query from stdin

```bash
printf '%s\n' '{"platform":"linux","status":"failed","log":"Start request repeated too quickly"}' \
  | linux/blackout-kb context --incident -
```

## Docker

```bash
docker build -t blackout-ops-kb:0.3.0 .
docker run --rm blackout-ops-kb:0.3.0 doctor
```

## NemoClaw / LangChain Deep Agents

On NemoClaw host:

```bash
nemo-deepagents <sandbox> skill install integrations/nemoclaw/blackout-ops-rag
nemo-deepagents <sandbox> skill list
nemo-deepagents launch <sandbox>
```

Start a new agent session after skill install. Skill is local and read-only.
It does not need network-policy exceptions.
