# 504 Gateway Timeout on /api/
Symptoms: `/` returns 200, `/api/` returns 504 (not 502). nginx logs show `upstream timed out (110: Connection timed
out) while reading response header from upstream`. 502 means the upstream refused the connection; 504 means it
accepted or never answered in time, so check that the upstream is alive before touching any config. A container shown
as `Up (Paused)` in `ops__docker_ps` is frozen: alive, but answering nothing, and every request to it times out.
Steps: 1) `ops__check_health` to confirm 200 on `/` and 504 on `/api/`.
2) `ops__docker_ps`: if `edge-victim-app` is Exited, this is really the upstream-down case, `ops__docker_start` it.
If it is `Paused`, `ops__docker_restart {"name": "edge-victim-app"}` unfreezes it (there is no unpause tool; restart
covers it).
3) If it is Up, `ops__docker_logs {"name": "edge-victim-app", "tail": 40}` for errors, and `ops__top_processes` to see
whether it is spinning on CPU.
4) `ops__read_config` and check the `/api/` `proxy_pass` target and any `proxy_read_timeout`.
5) Restart the upstream (`ops__docker_restart {"name": "edge-victim-app"}`), then verify `/api/` returns 200.
6) Still 504 with both containers Up and nothing in the upstream's log? Then nginx cannot reach it at all: the
upstream has fallen off the stack network. `ops__network_repair`, then `ops__docker_restart {"name": "edge-victim"}`
so nginx reconnects. (Runbook: upstream-detached.)
Do not restart the front-end for an upstream timeout before step 6.
