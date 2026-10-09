"""Run one policy over the fixed schedule on the SYNTHETIC toy env (greedy for SAC)."""
from __future__ import annotations

import argparse
import json
import os

import torch

from .contract import ACTIONS
from .masked_sac import MaskedDiscreteSAC
from .rollout import Recorder, run_episode
from .runbook import Runbook
from .toy_env import DATA_ORIGIN, ToyBoutiqueEnv

EVAL_ENV_SEED = 7


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", choices=["noop", "hpa", "runbook", "checkpoint"], required=True)
    ap.add_argument("--ckpt")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--schedule", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sched = json.load(open(a.schedule))
    os.makedirs(a.out, exist_ok=True)
    env = ToyBoutiqueEnv(EVAL_ENV_SEED, hpa=(a.policy == "hpa"))
    rb = Runbook()
    if a.policy == "checkpoint":
        agent = MaskedDiscreteSAC(0)
        agent.load_state_dict(torch.load(a.ckpt, weights_only=False))
        pol = lambda o, m: agent.act(o, m, greedy=True)  # noqa: E731
    elif a.policy == "runbook":
        pol = rb.act
    else:
        pol = lambda o, m: 0  # noqa: E731
    rec = Recorder(os.path.join(a.out, f"{a.tag}_transitions.jsonl"), a.tag)
    out = []
    for ep in sched["episodes"]:
        rb.reset()
        plan = {k: ep[k] for k in ("fault", "target", "severity", "L")}
        _, s = run_episode(env, pol, plan=plan, recorder=rec, episode=ep["episode"])
        s["actions"] = [{"tick": t, "action": ACTIONS[x][2], "phase": ph} for t, x, ph in s["actions"]]
        out.append({"tag": a.tag, "episode": ep["episode"], **s,
                    "schedule_sha256": sched["sha256"], "data_origin": DATA_ORIGIN})
    rec.close()
    with open(os.path.join(a.out, f"{a.tag}_episodes.json"), "w") as f:
        json.dump(out, f, indent=1)
    rec_rate = sum(o["recovered"] for o in out if o["fault"] != "NULL") / 24
    print(f"{a.tag}: fault recovery {rec_rate:.2f}")


if __name__ == "__main__":
    main()
