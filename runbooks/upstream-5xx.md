# Upstream answers with a 5xx of its own (500/503), container is Up
Symptoms: `/` returns 200, `/api/` returns 500 or 503 (not 502, not 504). `ops__docker_ps` shows `edge-victim-app` Up.
`ops__docker_logs {"name": "edge-victim-app"}` shows it serving `500` itself. The front end is fine; the upstream is
misconfigured or broken inside.
Steps: 1) `ops__docker_logs` of the upstream to confirm the 500 comes from it, not from nginx.
2) `ops__docker_restart {"name": "edge-victim-app"}` and re-check `/api/`.
3) If it still returns 5xx after a restart, the fault lives in the container's own writable layer (a config it
rewrote, a file it corrupted), and a restart keeps that layer. Rebuild it from its definition:
`ops__recreate_stack {"name": "edge-victim-app", "replace": true}`. This stack's containers hold no data, so nothing
is lost.
4) `ops__check_health`: both URLs 200.
