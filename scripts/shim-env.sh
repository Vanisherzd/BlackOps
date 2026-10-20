# restart the oai shim with the given env (e.g. SHIM_THINK=0), by pid file
cd ~/edge-agent
[ -f logs/oai-shim.pid ] && kill "$(cat logs/oai-shim.pid)" 2>/dev/null
pkill -f "scripts/oai_shim[.]py" 2>/dev/null; sleep 2
env $1 setsid nohup .venv/bin/python scripts/oai_shim.py > logs/oai-shim.out 2>&1 < /dev/null &
echo $! > logs/oai-shim.pid; sleep 3
curl -s -o /dev/null -w "shim %{http_code} [$1]\n" localhost:8001/v1/models
