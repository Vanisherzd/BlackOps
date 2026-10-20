# Bolt-on-vLLM Teammate Handoff

## Where things are

- On a Mac, work from the clone or worktree root; do not depend on a fixed absolute path.
- The DGX Spark is `hsnl@192.168.2.60`; this branch is the separate worktree `~/edge-agent-bolt`.
- Do not switch `~/edge-agent`: it is the team's `kb-retrieval-v2` checkout. `~/edge-agent-bolt/.venv` is a symlink to `~/edge-agent/.venv`.
- GitHub is remote `origin`. Push Mac → Spark with:
```sh
git push spark feat/blackbox-bolt-vllm
```
The Spark repository has `receive.denyCurrentBranch=updateInstead`, so its worktree updates without a pull.

## Read in this order

1. `docs/BOLT_RUNTIME_STATUS.md`
2. `docs/BOLT_VLLM_BASELINE.md` for reproduction steps and variables
3. `docs/ARCHITECTURE.md`
4. `docs/BOLT_VLLM_PLAN.md`
5. `docs/current_state.md`

## Step 0: the only blocker

On the Spark, enter `~/edge-agent-bolt` and run only this cleanup. Never run `docker system prune`; never delete
Hugging Face caches or images.
```sh
cd ~/edge-agent-bolt
docker builder prune -af
docker container prune -f
df -h /
```
Pass: `/` shows more than 60 GB free.

## Steps 1–10: baseline acceptance run

Run below from `~/edge-agent-bolt`. Every artifact must retain its `measurement: LOCAL_MEASUREMENT` environment block. Commit the artifacts and enter C=1/C=2 values in `docs/BOLT_VLLM_BASELINE.md`'s `LOCAL_MEASUREMENT` table.

### Step 1 — Ensure the image exists

```sh
docker image inspect blackbox-vllm-bolt:77154ca >/dev/null
```
If missing, and only then:
```sh
make bolt-build
```
Pass: Docker can inspect `blackbox-vllm-bolt:77154ca`.
### Step 2 — Start the C=1 baseline

```sh
make bolt-up
```
Pass: `vllm-bolt` starts with restart policy `no`.
### Step 3 — Gate 1: health and model listing

```sh
make bolt-wait
docker logs vllm-bolt > logs/bolt-startup.log 2>&1
```
Pass: the wait exits zero, `/health` succeeds, and `/v1/models` lists `bolt`.
### Step 4 — Recheck the harness

```sh
uv run --no-sync -m unittest discover -s tests
```
Pass: 33 tests report `OK`.
### Step 5 — Gates 2–3: chat, JSON, reasoning, and truncation

```sh
uv run --no-sync -m blackbox.benchmarks.inference_smoke --out artifacts/bolt_inference_smoke.json
```
Pass: all six checks pass, including exact `BLACKBOX_BOLT_OK`, reasoning on/off, and explicit truncation.
### Step 6 — Gate 4: structured tool call

```sh
uv run --no-sync -m blackbox.benchmarks.tool_call_smoke --out artifacts/bolt_tool_call_smoke.json
```
Pass: `single_tool_call` has one parser-produced valid call and arguments.
### Step 7 — Gates 5–6: multi-turn and incident loops

```sh
uv run --no-sync -m blackbox.benchmarks.multi_turn_smoke --out artifacts/bolt_multi_turn_smoke.json
uv run --no-sync - <<'PY'
from blackbox.agent import run_incident
from blackbox.benchmarks.common import write_artifact
from blackbox.inference import InferenceGateway
from blackbox.tools.mock_tools import MockWorld, build_registry
w = MockWorld().scenario("api_down"); g = InferenceGateway()
s = run_incident("Resolve api_down; read, repair, verify, then finish.", g, build_registry(w), verify=lambda _s, x: bool(x and x.services["api"]["healthy"]))
write_artifact("artifacts/bolt_incident_api_down.json", {"benchmark": "incident_api_down", "state": s.to_dict()}, g)
assert s.status == "resolved" and w.services["api"]["healthy"]
PY
```
Pass: both smoke cycles pass and the incident ends `resolved`. Gate 5 closes only when Step 9 also proves the
read → restart → read cases; one call plus a final answer is insufficient.
### Step 8 — Concurrency smoke

```sh
uv run --no-sync -m blackbox.benchmarks.concurrency_smoke --out artifacts/bolt_concurrency_smoke.json
```
Pass: all sequential and two-thread requests return exactly `BLACKBOX_BOLT_OK`.
### Step 9 — Gates 5, 7, and 8: evaluation and C=1 performance

```sh
uv run --no-sync -m blackbox.benchmarks.tool_eval --out-json artifacts/bolt_tool_eval.json --out-md artifacts/bolt_tool_eval.md
uv run --no-sync -m blackbox.benchmarks.perf_baseline --concurrency 1 --startup-log logs/bolt-startup.log --out artifacts/bolt_perf_c1.json
```
Pass: all 27 cases run, continuation/verification passes, and all six C=1 requests record startup, TTFT,
output-token/s, ITL, memory, token, and wall-time fields.
### Step 10 — Gate 9: C=2 performance, then stop

```sh
make bolt-down
MAX_NUM_SEQS=2 make bolt-up
make bolt-wait
uv run --no-sync -m blackbox.benchmarks.perf_baseline --concurrency 2 --out artifacts/bolt_perf_c2.json
make bolt-down
```
Pass: all six C=2 requests succeed, the C=2 row is recorded, and `vllm-bolt` is removed.

## If something fails

```sh
make bolt-logs
deploy/vllm/healthcheck.sh --wait 60
```
`logs/bolt-args.txt` holds the exact argv. Suspect, in order: incomplete model download; a b12x/FLASHINFER backend
error on this GPU; reasoning-parser plugin path; `max_model_len` versus memory. Report the failing gate number, log
tail, and artifact, not a narrative.

## Rules that must not change

- No restart policy and nothing at boot.
- Do not edit `scripts/`, `nemoclaw/`, `skills/`, `runbooks/`, `kb/`, or `trt/` on this branch.
- Destructive tools stay denied. Commit artifacts only with their environment block.
- Model-card numbers are never results.

## Continue with the same workflow

Use one Codex CLI worktree per task. Briefs name files to create and directories not to touch; a human reviews and
merges.
```sh
git worktree add ../edge-agent-wt-X -b codex/X feat/blackbox-bolt-vllm
codex exec -s workspace-write -C <worktree> - < brief.md
```

## Phase 7: Gate 10, DFlash only after Gates 1–9 pass

The LiLiCorr drafter is 0.96 GB. Start with the model card's exact compact JSON:
```sh
BOLT_EXTRA_ARGS='--speculative-config {"method":"dflash","model":"nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-LiLiCorr","num_speculative_tokens":7,"rejection_sample_method":"standard","attention_backend":"FLASHINFER","draft_load_config":{"load_format":"auto"}}' make bolt-up
make bolt-wait
docker logs vllm-bolt > logs/bolt-startup-dflash.log 2>&1
```
Rerun Gates 2–8, naming outputs `artifacts/bolt_*_dflash.json` and the tool-eval Markdown
`artifacts/bolt_tool_eval_dflash.md`, then stop:
```sh
make bolt-down
```
Keep DFlash only if its `tool_eval` correctness metrics do not drop from the baseline.
