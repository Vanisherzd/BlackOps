"""Drop each MoE layer's least-used routed experts from the NVFP4 Nano checkpoint, without requantising.

  .venv/bin/python scripts/prune-experts.py 112       # -> logs/prune-calib/nano-nvfp4-e112/

The NVFP4 checkpoint stores every expert's quantised weights and scales as separate tensors and keeps the router in
BF16, so pruning is slicing: keep the KEEP experts per layer with the largest routing mass on the workload (drill
sessions + telemetry, from scripts/moe-routing-stats.py collect), renumber them 0..KEEP-1, take the same rows of
gate.weight and gate.e_score_correction_bias, and set n_routed_experts. The model's router (sigmoid scores plus
correction bias, top-6, normalised over the chosen six, one group) then picks among the survivors; nothing else in
the network changes, so a drill comparison against the unpruned checkpoint isolates the pruning.
"""
import json
import pathlib
import re
import shutil
import sys

import numpy as np
from huggingface_hub import snapshot_download
from safetensors import safe_open
from safetensors.torch import save_file

ROOT = pathlib.Path(__file__).resolve().parent.parent
COUNTS = ROOT / "logs" / "prune-calib" / "counts.npz"
SRC = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4"
EXPERT = re.compile(r"^backbone\.layers\.(\d+)\.mixer\.experts\.(\d+)\.(.+)$")
GATE = re.compile(r"^backbone\.layers\.(\d+)\.mixer\.gate\.(weight|e_score_correction_bias)$")


def keep_sets(keep):
    d = np.load(COUNTS)
    mass = d["A1_mass"] + d["A2_mass"] + d["B1_mass"] + d["B2_mass"]
    layers = [int(re.search(r"layers\.(\d+)\.", n).group(1)) for n in d["layers"]]
    out = {}
    for row, layer in enumerate(layers):
        kept = np.sort(np.argsort(-mass[row])[:keep])          # original order, so renumbering is monotone
        out[layer] = {int(old): new for new, old in enumerate(kept)}
        share = mass[row, kept].sum() / mass[row].sum()
        print(f"layer {layer:2d}: keep {keep}, routing mass kept {100 * share:.2f}%")
    return out


def main(keep):
    src = pathlib.Path(snapshot_download(SRC))
    dst = ROOT / "logs" / "prune-calib" / f"nano-nvfp4-e{keep}"
    dst.mkdir(parents=True, exist_ok=True)
    keepmap = keep_sets(keep)
    index = json.load(open(src / "model.safetensors.index.json"))
    weight_map, total = {}, 0
    for shard in sorted(src.glob("*.safetensors")):
        out = {}
        with safe_open(str(shard), framework="pt", device="cpu") as f:
            for key in f.keys():
                m = EXPERT.match(key)
                if m:
                    new = keepmap[int(m.group(1))].get(int(m.group(2)))
                    if new is None:
                        continue
                    name = f"backbone.layers.{m.group(1)}.mixer.experts.{new}.{m.group(3)}"
                    out[name] = f.get_tensor(key)
                    continue
                t = f.get_tensor(key)
                g = GATE.match(key)
                if g:
                    rows = sorted(keepmap[int(g.group(1))])
                    t = t[rows].contiguous()
                out[key] = t
        save_file(out, str(dst / shard.name), metadata={"format": "pt"})
        for k, t in out.items():
            weight_map[k] = shard.name
            total += t.numel() * t.element_size()
        print(f"{shard.name}: {len(out)} tensors", flush=True)
    index["weight_map"], index["metadata"] = weight_map, {"total_size": total}
    json.dump(index, open(dst / "model.safetensors.index.json", "w"), indent=2)
    cfg = json.load(open(src / "config.json"))
    assert cfg["n_group"] == 1, "grouped routing would need the groups re-cut"
    cfg["n_routed_experts"] = keep
    json.dump(cfg, open(dst / "config.json", "w"), indent=2)
    for p in src.iterdir():
        if p.suffix != ".safetensors" and p.name not in ("config.json", "model.safetensors.index.json"):
            shutil.copyfile(p.resolve(), dst / p.name)
    (dst / "PRUNED.json").write_text(json.dumps(
        {"source": SRC, "kept_per_layer": keep, "criterion": "routing mass on drill sessions + telemetry",
         "kept": {str(l): sorted(m) for l, m in keepmap.items()}}, indent=1))
    print(f"wrote {dst} ({total / 2**30:.1f} GiB)")


if __name__ == "__main__":
    main(int(sys.argv[1]))
