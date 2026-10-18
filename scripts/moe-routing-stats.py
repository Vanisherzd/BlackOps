"""Which of Nemotron-3-Nano's 128 routed experts does this workload use? A feasibility check for expert pruning.

  .venv/bin/python scripts/moe-routing-stats.py collect   # BF16 checkpoint, router hooks -> logs/prune-calib/counts.npz
  .venv/bin/python scripts/moe-routing-stats.py report    # coverage, overlap, held-out check -> docs/moe-routing-stats.md

Expert pruning only pays if the workload concentrates on a subset of experts. Measured here, on three sources:
  A  the agent's own drill sessions (80 Nano transcripts from the sandbox: alert, thinking, tool calls, tool results),
     split by session into A1 / A2 so the stability of the statistic can be checked;
  B  the knowledge-pack evaluation's compressed telemetry (logs/kb-eval/inputs/ratio_8.jsonl), split B1 / B2;
  C  a control with nothing to do with operations: GSM8K and IFEval prompts.
Every routed choice of every token is counted per layer (NemotronHTopkRouter's top-6 indices) together with its
routing weight. Inputs are plain role-tagged text capped at MAX_TOKENS per sample, forward only, no generation.

The question the report answers: if each MoE layer kept only the K experts most used on A1+B1, what share of the
routing on the held-out A2+B2 would still land on a kept expert? And is that set specific to the workload (does the
control's top-K differ)? This is routing coverage, not accuracy: a pruned model still has to be measured on the drills.
"""
import collections
import json
import os
import pathlib
import random
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "logs" / "prune-calib"
COUNTS = OUT / "counts.npz"
REPORT = ROOT / "docs" / "moe-routing-stats.md"
MODEL = os.environ.get("BF16_MODEL", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "3000"))
KS = (16, 32, 48, 64, 96)


# --- calibration text -----------------------------------------------------------------------------------------------

def session_text(path):
    """A sandbox session transcript as the role-tagged text the model saw, in order."""
    parts = []
    for line in open(path, encoding="utf-8"):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        m = row.get("message") or {}
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            parts.append(f"<{role}>\n{content}")
            continue
        for c in content or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text" and c.get("text"):
                parts.append(f"<{role}>\n{c['text']}")
            elif c.get("type") == "thinking" and c.get("thinking"):
                parts.append(f"<think>\n{c['thinking']}\n</think>")
            elif c.get("type") == "toolCall":
                parts.append(f"<tool_call>{c.get('name')} {json.dumps(c.get('arguments'), ensure_ascii=False)}</tool_call>")
            elif c.get("type") == "toolResult" or role == "toolResult":
                txt = c.get("text") or json.dumps(c.get("content"), ensure_ascii=False)
                parts.append(f"<tool_result>\n{txt}")
    return "\n".join(parts)


def telemetry_text(row):
    lines = []
    for e in row.get("telemetry") or []:
        msg = str(e.get("template") or e.get("message") or "").replace("\n", " ")[:600]
        lines.append(f"[{e.get('kind')}] {e.get('service') or ''}: {msg}")
    return "Telemetry from one incident:\n" + "\n".join(lines)


def control_texts():
    os.environ.setdefault("HF_DATASETS_OFFLINE", "1")     # the cache has them; never wait on the Hub
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from datasets import load_dataset
    out = []
    g = load_dataset("openai/gsm8k", "main", split="test")
    buf = ""
    for ex in g.select(range(400)):
        buf += f"Question: {ex['question']}\nAnswer: {ex['answer']}\n\n"
        if len(buf) > 9000:
            out.append(buf); buf = ""
    try:
        f = load_dataset("google/IFEval", split="train")
        buf = ""
        for ex in f.select(range(300)):
            buf += ex["prompt"] + "\n\n"
            if len(buf) > 9000:
                out.append(buf); buf = ""
    except Exception as e:                       # IFEval is optional; GSM8K alone is a control
        print(f"ifeval unavailable: {e}", file=sys.stderr)
    return out


def datasets_():
    sess = sorted((OUT / "sessions").glob("*.jsonl"))
    random.Random(0).shuffle(sess)
    half = len(sess) // 2
    a = [session_text(p) for p in sess]
    tel = [json.loads(l) for l in open(ROOT / "logs" / "kb-eval" / "inputs" / "ratio_8.jsonl", encoding="utf-8")]
    random.Random(1).shuffle(tel)
    b = [telemetry_text(r) for r in tel]
    hb = len(b) // 2
    return {"A1": a[:half], "A2": a[half:], "B1": b[:hb], "B2": b[hb:], "C": control_texts()}


# --- collect --------------------------------------------------------------------------------------------------------

MIN_AVAILABLE_GB = float(os.environ.get("MIN_AVAILABLE_GB", "12"))


def mem_available_gb():
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 2**20
    return 0.0


def guard_memory(shards):
    """The Spark has no swap and one memory pool for CPU and GPU: the mmapped safetensors' page cache (59 GB) plus
    the GPU copy (59 GB) livelocked the machine once. Keep the shards out of the page cache while loading, and exit
    hard before MemAvailable runs out rather than let the kernel thrash."""
    import threading

    def run():
        while True:
            for p in shards:
                try:
                    fd = os.open(p, os.O_RDONLY)
                    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                    os.close(fd)
                except OSError:
                    pass
            avail = mem_available_gb()
            if avail < MIN_AVAILABLE_GB:
                print(f"MemAvailable {avail:.1f} GB < {MIN_AVAILABLE_GB} GB, aborting", file=sys.stderr, flush=True)
                os._exit(3)
            time.sleep(2)
    threading.Thread(target=run, daemon=True).start()


def load_model(path):
    """The BF16 checkpoint straight onto the GPU, one tensor at a time. transformers' own loader stages its expert
    fusion (128 per-expert matrices into one tensor per layer) in CPU memory it does not give back, which on this
    machine is the same memory the GPU copy lives in; it ran out at 65 % of the weights."""
    import torch
    from safetensors import safe_open
    from transformers import AutoConfig, AutoModelForCausalLM
    cfg = AutoConfig.from_pretrained(path, trust_remote_code=False)
    with torch.device("meta"):
        model = AutoModelForCausalLM.from_config(cfg, trust_remote_code=False, dtype=torch.bfloat16)
    persistent = set(model.state_dict())
    stray = [n for n, _ in model.named_buffers() if n not in persistent]
    if stray:
        raise SystemExit(f"non-persistent buffers would be left uninitialised: {stray[:5]}")
    model.to_empty(device="cuda:0")
    params = model.state_dict()
    done = set()
    expert = re.compile(r"^(.*\.experts)\.(\d+)\.(up_proj|down_proj)\.weight$")
    for shard in sorted(pathlib.Path(path).glob("*.safetensors")):
        with safe_open(str(shard), framework="pt", device="cpu") as f:
            for key in f.keys():
                name = "model." + key[len("backbone."):] if key.startswith("backbone.") else key
                m = expert.match(name)
                with torch.no_grad():
                    if m:
                        target = f"{m.group(1)}.{m.group(3)}"
                        params[target][int(m.group(2))].copy_(f.get_tensor(key))
                        done.add(target)
                    else:
                        params[name].copy_(f.get_tensor(key))
                        done.add(name)
    missing = persistent - done
    if missing:
        raise SystemExit(f"weights not in the checkpoint: {sorted(missing)[:5]}")
    if cfg.tie_word_embeddings:
        model.tie_weights()
    return model


def collect(_):
    import numpy as np
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer
    OUT.mkdir(parents=True, exist_ok=True)
    data = datasets_()
    print({k: len(v) for k, v in data.items()}, flush=True)
    path = snapshot_download(MODEL)
    guard_memory([os.path.realpath(p) for p in pathlib.Path(path).glob("*.safetensors")])
    # transformers' own NemotronH, not the checkpoint's remote code: that one refuses to import without mamba-ssm,
    # which has no aarch64 wheel here; the native one runs the Mamba mixer in plain torch.
    tok = AutoTokenizer.from_pretrained(MODEL)
    t0 = time.time()
    model = load_model(path)
    model.eval()
    print(f"MemAvailable after load: {mem_available_gb():.1f} GB", flush=True)
    print(f"loaded in {time.time() - t0:.0f}s", flush=True)
    routers = [(n, m) for n, m in model.named_modules() if type(m).__name__.endswith("TopkRouter")]
    n_exp = model.config.n_routed_experts
    print(f"{len(routers)} routers, {n_exp} experts each", flush=True)
    current = {}

    def hook(idx):
        def fn(_mod, _inp, out):
            # native router: (logits, weights, indices); the checkpoint's remote code: (indices, weights)
            _, w, ind = out if len(out) == 3 else (None, out[1], out[0])
            current["cnt"][idx] += torch.bincount(ind.reshape(-1), minlength=n_exp).cpu().numpy()
            current["mass"][idx] += torch.zeros(n_exp, device=w.device, dtype=torch.float32).scatter_add_(
                0, ind.reshape(-1), w.reshape(-1).float()).cpu().numpy()
        return fn
    for i, (_, m) in enumerate(routers):
        m.register_forward_hook(hook(i))
    result, tokens = {}, {}
    for name, texts in data.items():
        current["cnt"] = np.zeros((len(routers), n_exp), dtype=np.int64)
        current["mass"] = np.zeros((len(routers), n_exp), dtype=np.float64)
        ntok, ts = 0, time.time()
        for j, text in enumerate(texts):
            ids = tok(text, return_tensors="pt", truncation=True, max_length=MAX_TOKENS).input_ids.to(0)
            with torch.no_grad():
                model(input_ids=ids, use_cache=False)
            ntok += ids.shape[1]
            if j % 5 == 0:
                print(f"  {name} {j + 1}/{len(texts)} tokens={ntok} {ntok / (time.time() - ts):.0f} tok/s", flush=True)
        result[f"{name}_cnt"], result[f"{name}_mass"] = current["cnt"].copy(), current["mass"].copy()
        tokens[name] = ntok
        print(f"{name}: {len(texts)} samples, {ntok} tokens, {time.time() - ts:.0f}s", flush=True)
    np.savez(COUNTS, **result, layers=np.array([n for n, _ in routers]))
    (OUT / "tokens.json").write_text(json.dumps(tokens))
    print(f"saved {COUNTS}")


# --- report ---------------------------------------------------------------------------------------------------------

def report(_):
    import numpy as np
    d = np.load(COUNTS, allow_pickle=False)
    tokens = json.loads((OUT / "tokens.json").read_text())
    cnt = {k[:-4]: d[k] for k in d.files if k.endswith("_cnt")}
    L, E = cnt["A1"].shape

    def topk_sets(c, k):
        return [set(np.argsort(-c[l])[:k].tolist()) for l in range(L)]

    def coverage(choose, evaluate, k):
        """Per layer: share of `evaluate`'s routed choices that land on the top-k experts of `choose`."""
        sets = topk_sets(choose, k)
        return np.array([evaluate[l, list(sets[l])].sum() / max(1, evaluate[l].sum()) for l in range(L)])

    def jacc(x, y, k):
        a, b = topk_sets(x, k), topk_sets(y, k)
        return np.array([len(a[l] & b[l]) / len(a[l] | b[l]) for l in range(L)])

    calib = cnt["A1"] + cnt["B1"]
    held = cnt["A2"] + cnt["B2"]
    lines = ["# Nemotron-3-Nano MoE routing on this workload (expert-pruning feasibility)", "",
             f"Generated by `scripts/moe-routing-stats.py report`. {L} MoE layers x {E} routed experts, top-6 per token. "
             f"Tokens: {tokens}. A = agent drill sessions, B = compressed incident telemetry, C = GSM8K + IFEval control; "
             "1/2 = disjoint halves. Coverage = share of routed choices that land on the kept experts.", "",
             "## Held-out coverage: keep each layer's top-K on A1+B1, measure on A2+B2", "",
             "| K kept of 128 | median layer | worst layer | mean |", "|---:|---:|---:|---:|"]
    for k in KS:
        c = coverage(calib, held, k)
        lines.append(f"| {k} | {100 * np.median(c):.1f}% | {100 * c.min():.1f}% | {100 * c.mean():.1f}% |")
    lines += ["", "## Is the set specific to the workload?", "",
              "Same K, experts chosen on the control (C) instead, coverage of the held-out workload (A2+B2); and the "
              "workload's set applied to the control.", "",
              "| K | workload set on workload | control set on workload | workload set on control |", "|---:|---:|---:|---:|"]
    for k in KS:
        lines.append(f"| {k} | {100 * coverage(calib, held, k).mean():.1f}% | {100 * coverage(cnt['C'], held, k).mean():.1f}% | "
                     f"{100 * coverage(calib, cnt['C'], k).mean():.1f}% |")
    lines += ["", "## Stability and overlap of the top-64 sets (Jaccard, mean over layers)", "",
              f"- A1 vs A2 (two halves of the drill sessions): {jacc(cnt['A1'], cnt['A2'], 64).mean():.2f}",
              f"- B1 vs B2 (two halves of the telemetry): {jacc(cnt['B1'], cnt['B2'], 64).mean():.2f}",
              f"- A vs B (drills vs telemetry): {jacc(cnt['A1'] + cnt['A2'], cnt['B1'] + cnt['B2'], 64).mean():.2f}",
              f"- workload (A+B) vs control (C): {jacc(calib + held, cnt['C'], 64).mean():.2f}", "",
              "## Unused experts", ""]
    allw = calib + held
    share = allw / allw.sum(axis=1, keepdims=True)
    lines.append(f"- experts never routed to on the workload, per layer: median {int(np.median((allw == 0).sum(1)))}, "
                 f"max {int((allw == 0).sum(1).max())}")
    lines.append(f"- experts under 0.1 % of a layer's routing: median {int(np.median((share < 0.001).sum(1)))}, "
                 f"max {int((share < 0.001).sum(1).max())}")
    lines += ["", "## Per layer (held-out coverage at K = 64 and 96)", "", "| layer | K=64 | K=96 |", "|---|---:|---:|"]
    c64, c96 = coverage(calib, held, 64), coverage(calib, held, 96)
    for l in range(L):
        lines.append(f"| {l} | {100 * c64[l]:.1f}% | {100 * c96[l]:.1f}% |")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:40]))


if __name__ == "__main__":
    {"collect": collect, "report": report}[sys.argv[1]](sys.argv)
