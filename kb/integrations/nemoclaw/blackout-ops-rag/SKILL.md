---
name: blackout-ops-rag
description: Search local Blackout Ops Linux and Kubernetes incident cases, runbooks, and service topology before proposing diagnosis or repair.
---

# Blackout Ops local RAG

Use this skill for Linux, systemd, disk-full, OOM, DNS, TLS, port-listener, or
Kubernetes CrashLoopBackOff incidents.

## Required workflow

1. Gather current read-only evidence: platform, host/namespace, resource,
   exact error, exit code/reason, time window, and checks already run.
2. Create incident JSON under `/tmp`; never include secrets, private keys,
   tokens, kubeconfig content, or plaintext Secret values.
3. Run:

```bash
python3 /sandbox/.deepagents/agent/skills/blackout-ops-rag/scripts/query_kb.py \
  context --incident /tmp/blackout-incident.json
```

4. Inspect `knowledge_match.status`.
   - `matched`: use `cases`, `solutions`, and `topologies` as hypotheses only.
   - `insufficient`: perform more R-level checks or escalate. Never invent or
     execute a repair from `weak_solution_candidates`.
5. Confirm root cause from current environment evidence. Historical cases are
   not current facts.
6. Return proposed plan with supporting/contradicting evidence, missing checks,
   blast radius, rollback, and verification.

## Safety

- R: read-only; may run inside sandbox.
- W: state-changing; require project approval and audit.
- D: destructive or broad-impact; require explicit human approval.
- Knowledge tool is read-only and `plan_only`. Never directly execute a
  remediation because a retrieved case looks similar.
