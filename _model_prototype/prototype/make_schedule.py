"""Fixed evaluation schedule (PLAN.md M5): 6 × each of F1–F4, balanced over
targets/severities, + 6 NULL = 30 episodes, seed 2026.

This one is NOT synthetic data: it is the same kind of plan the real
eval/make_schedule.py will produce, and could be reused on the cluster.
"""
from __future__ import annotations

import argparse
import hashlib
import json

import numpy as np

from .contract import CONTRACT

EP = CONTRACT["episode"]


def make(seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    eps = []
    for sev in EP["f1_latency"] * 2:
        eps.append({"fault": "F1", "target": "productcatalogservice", "severity": sev})
    f2 = [(t, w) for t in EP["f2_targets"] for w in EP["f2_workers"]]
    for t, w in f2 + [f2[1], f2[3]]:
        eps.append({"fault": "F2", "target": t, "severity": w})
    for t in EP["f3_targets"] * 3:
        eps.append({"fault": "F3", "target": t, "severity": None})
    for m in EP["f4_multiplier"] * 3:
        eps.append({"fault": "F4", "target": "frontend", "severity": m})
    eps += [{"fault": "NULL", "target": None, "severity": None} for _ in range(6)]
    order = rng.permutation(len(eps))
    sched = []
    for i, j in enumerate(order):
        e = dict(eps[j])
        e["episode"] = i
        e["L"] = int(rng.integers(EP["lead_in_ticks"][0], EP["lead_in_ticks"][1] + 1))
        sched.append(e)
    return sched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sched = make(a.seed)
    body = json.dumps(sched, sort_keys=True)
    doc = {"seed": a.seed, "sha256": hashlib.sha256(body.encode()).hexdigest(), "episodes": sched}
    with open(a.out, "w") as f:
        json.dump(doc, f, indent=1)
    print(f"wrote {len(sched)} episodes, sha256={doc['sha256'][:12]}")


if __name__ == "__main__":
    main()
