# Upstream answers with a 5xx of its own (500/503), container is Up
Symptoms: `/` returns 200, `/api/` returns 500 or 503 (not 502, not 504). `ops__docker_ps` shows `edge-victim-app` Up.
`ops__docker_logs {"name": "edge-victim-app"}` shows it serving `500` itself: access-log lines like
`"GET / HTTP/1.0" 500 177 "-" "Python-urllib/3.12"` with no error line before them. The service itself answers HTTP 500
to requests while the container is up. The front end is fine; the upstream is misconfigured or broken inside.
Steps: 1) `ops__docker_logs` of the upstream to confirm the 500 comes from it, not from nginx.
2) `ops__docker_restart {"name": "edge-victim-app"}` and re-check `/api/`.
3) If it still returns 5xx after a restart, the fault lives in the container's own writable layer (a config it
rewrote, a file it corrupted), and a restart keeps that layer. Put the file back in place from the image, which keeps
the container: `ops__repair_image_file {"name": "edge-victim-app", "path": "/etc/nginx/conf.d/default.conf"}`
(`ops__docker_exec {"name": "edge-victim-app", "cmd": "cat /etc/nginx/conf.d/default.conf"}` shows what it rewrote).
4) Only if /api/ still fails after the file repair, rebuild it from its definition:
`ops__recreate_stack {"name": "edge-victim-app", "replace": true}`. This stack's containers hold no data.
5) `ops__check_health`: both URLs 200.
