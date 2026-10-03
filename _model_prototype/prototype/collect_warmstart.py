"""Collect the warm-start dataset: runbook with ε = 0.25 for 120 episodes (CLAUDE.md §5.11)."""
from __future__ import annotations

import argparse
import json
import os

import numpy as np

from .contract import CONTRACT
from .rollout import Recorder, run_episode
from .runbook import Runbook
from .toy_env import ToyBoutiqueEnv

RB = CONTRACT["runbook"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=100)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    env = ToyBoutiqueEnv(a.seed)
    rb = Runbook(epsilon=RB["warmstart_epsilon"], rng=np.random.default_rng(a.seed + 1))
    rec = Recorder(os.path.join(a.out, "transitions.jsonl"), "warmstart")
    summaries = []
    for e in range(RB["warmstart_episodes"]):
        rb.reset()
        _, s = run_episode(env, rb.act, recorder=rec, episode=e,
                           src_fn=lambda: (1, "runbook") if rb.last_source == "runbook" else (2, "eps"))
        s.pop("actions")
        summaries.append({"episode": e, **s})
    rec.close()
    with open(os.path.join(a.out, "episodes.json"), "w") as f:
        json.dump(summaries, f, indent=1)
    rr = np.mean([s["recovered"] for s in summaries if s["fault"] != "NULL"])
    print(f"warm-start: {len(summaries)} episodes, fault recovery {rr:.2f}")


if __name__ == "__main__":
    main()
