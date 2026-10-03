"""Episode runner + JSONL recorder shared by collection, training and evaluation."""
from __future__ import annotations

import json

import numpy as np

from .toy_env import DATA_ORIGIN


def _r(x):
    return [round(float(v), 4) for v in x]


class Recorder:
    def __init__(self, path: str, run: str):
        self.f = open(path, "w")
        self.run = run

    def write(self, episode: int, rec: dict, info: dict):
        line = {
            "run": self.run, "episode": episode, "tick": info["tick"], "phase": info["phase"],
            "fault": info["fault"], "target": info["target"], "severity": info["severity"],
            "source": rec.get("src_name", "policy"), "obs": _r(rec["s"]),
            "mask": [int(v) for v in rec["m"]], "a_chosen": info["a_chosen"],
            "a_exec": info["a_exec"], "reward": round(rec["r"], 5), "next_obs": _r(rec["s2"]),
            "next_mask": [int(v) for v in rec["m2"]], "terminated": rec["terminated"],
            "truncated": rec["truncated"], "valid": info["valid"], "stale": info["stale"],
            "raw": {"p99_ms": round(info["raw_p99_ms"], 1), "fail": round(info["raw_fail"], 4),
                    "rps": round(info["raw_rps"], 2), "spec": info["spec"]},
            "v": round(info["v"], 4), "healthy": info["healthy"], "data_origin": DATA_ORIGIN,
        }
        self.f.write(json.dumps(line) + "\n")
        self.f.flush()

    def close(self):
        self.f.close()


def run_episode(env, policy_fn, plan=None, on_step=None, recorder=None, episode=0, src_fn=None):
    """policy_fn(obs, mask) -> action. Returns (records, summary)."""
    o, m, info0 = env.reset(plan)
    plan = info0["plan"]
    recs, ret, n_act = [], 0.0, 0
    sum_v, actions = 0.0, []
    while True:
        a = policy_fn(o, m)
        o2, m2, r, te, tr, info = env.step(a)
        rec = {"s": o, "m": m.astype(bool), "a": info["a_exec"], "r": r, "s2": o2,
               "m2": m2.astype(bool), "terminated": te, "truncated": tr,
               "stale2": bool(info["stale"]), "src": 0, "src_name": "policy"}
        if src_fn is not None:
            rec["src"], rec["src_name"] = src_fn()
        recs.append(rec)
        if recorder is not None:
            recorder.write(episode, rec, info)
        ret += r
        if info["phase"] != "lead_in" or plan["fault"] == "NULL":
            sum_v += info["v"]
        if info["a_exec"]:
            n_act += 1
            actions.append((info["tick"], info["a_exec"], info["phase"]))
        if on_step is not None:
            on_step()
        o, m = o2, m2
        if te or tr:
            break
    summ = {"fault": plan["fault"], "target": plan["target"], "severity": plan["severity"],
            "L": plan["L"], "return": ret, "recovered": bool(info["recovered"]),
            "mttr_s": info["mttr_s"], "n_actions": n_act, "sum_v": sum_v, "ticks": len(recs),
            "valid": info["valid"], "stale_ticks": int(sum(r["stale2"] for r in recs)),
            "actions": actions}
    return recs, summ


def np_rng(seed):
    return np.random.default_rng(seed)
