"""Warm-start + online training of masked discrete SAC on the SYNTHETIC toy env.

Usage: python -m prototype.train --replay per --seed 0 --episodes 300 \
           --warmstart data/warmstart --out runs/sac_per_s0
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import time
from collections import defaultdict

import numpy as np
import torch

from .contract import CONTRACT
from .masked_sac import MaskedDiscreteSAC
from .per_buffer import ReplayBuffer, nstep_transitions
from .rollout import Recorder, run_episode
from .toy_env import DATA_ORIGIN, ToyBoutiqueEnv

RL = CONTRACT["rl"]
PROBE_EVERY, PROBE_EPISODES = 75, 5


def load_warmstart(path: str) -> list[list[dict]]:
    eps = defaultdict(list)
    with open(os.path.join(path, "transitions.jsonl")) as f:
        for line in f:
            j = json.loads(line)
            eps[j["episode"]].append({
                "s": np.array(j["obs"], np.float32), "m": np.array(j["mask"], bool),
                "a": j["a_exec"], "r": j["reward"], "s2": np.array(j["next_obs"], np.float32),
                "m2": np.array(j["next_mask"], bool), "terminated": j["terminated"],
                "stale2": j["stale"], "src": 1 if j["source"] == "runbook" else 2})
    return [eps[k] for k in sorted(eps)]


def add_episode(buf: ReplayBuffer, recs: list[dict]) -> int:
    trs = nstep_transitions(recs, RL["gamma"], RL["n_step"])
    for t in trs:
        buf.add(*t)
    return len(trs)


def probe(agent, seed: int) -> dict:
    env = ToyBoutiqueEnv(seed)
    out = []
    for _ in range(PROBE_EPISODES):
        _, s = run_episode(env, lambda o, m: agent.act(o, m, greedy=True))
        out.append(s)
    faults = [s for s in out if s["fault"] != "NULL"]
    return {"probe_return": float(np.mean([s["return"] for s in out])),
            "probe_recovery": float(np.mean([s["recovered"] for s in faults])) if faults else float("nan")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", choices=["uniform", "per"], required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--episodes", type=int, default=300)
    ap.add_argument("--warmstart", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    random.seed(a.seed); np.random.seed(a.seed); torch.manual_seed(a.seed)
    rng = np.random.default_rng(a.seed)
    agent = MaskedDiscreteSAC(a.seed)
    buf = ReplayBuffer(RL["buffer_size"], a.replay == "per", rng)

    n = sum(add_episode(buf, ep) for ep in load_warmstart(a.warmstart))
    print(f"[{a.out}] warm-start buffer: {n} transitions")
    last = {}

    def learn_once():
        nonlocal last
        if buf.size < RL["batch_size"]:
            return
        idx, w, b = buf.sample(RL["batch_size"], agent.grad_step)
        last = agent.update(b, w)
        if buf.prioritized:
            buf.update_priorities(idx, last["td"])

    t0 = time.time()
    for _ in range(RL["offline_warmstart_steps"]):
        learn_once()
    print(f"offline warm-start done in {time.time() - t0:.0f}s, alpha={agent.alpha:.4f}")

    run = os.path.basename(a.out.rstrip("/"))
    rec = Recorder(os.path.join(a.out, "train_transitions.jsonl"), run)
    env = ToyBoutiqueEnv(1000 + a.seed)
    rows = []
    p = probe(agent, 5000)
    probes = [{"episode": 0, **p}]

    def on_step():
        for _ in range(RL["updates_per_tick"]):
            learn_once()

    for e in range(1, a.episodes + 1):
        recs, s = run_episode(env, lambda o, m: agent.act(o, m, greedy=False),
                              on_step=on_step, recorder=rec, episode=e)
        add_episode(buf, recs)
        row = {"episode": e, "fault": s["fault"], "target": s["target"], "severity": s["severity"],
               "return": round(s["return"], 4), "recovered": int(s["recovered"]),
               "mttr_s": s["mttr_s"], "n_actions": s["n_actions"],
               "false_remediation": int(s["fault"] == "NULL" and s["n_actions"] > 0),
               "sum_v": round(s["sum_v"], 4), "ticks": s["ticks"], "stale_ticks": s["stale_ticks"],
               "alpha": round(last.get("alpha", agent.alpha), 5),
               "entropy": round(last.get("entropy", float("nan")), 4),
               "q_mean": round(last.get("q_mean", float("nan")), 4),
               "loss_q": round(last.get("loss_q", float("nan")), 5),
               "frac_single_valid": round(last.get("frac_excluded", float("nan")), 3),
               "beta": round(buf.beta(agent.grad_step), 4) if buf.prioritized else None,
               "max_priority": round(buf.max_prio, 4) if buf.prioritized else None,
               "grad_step": agent.grad_step, "data_origin": DATA_ORIGIN}
        rows.append(row)
        if e % PROBE_EVERY == 0:
            probes.append({"episode": e, **probe(agent, 5000 + e)})
        if e % 25 == 0:
            torch.save(agent.state_dict(), os.path.join(a.out, "latest.pt"))
            rr = np.mean([r["recovered"] for r in rows[-25:] if r["fault"] != "NULL"] or [0])
            print(f"ep {e:3d}  rec25={rr:.2f}  alpha={agent.alpha:.4f}  "
                  f"grad={agent.grad_step}  {time.time() - t0:.0f}s", flush=True)
    rec.close()
    torch.save(agent.state_dict(), os.path.join(a.out, "final.pt"))
    with open(os.path.join(a.out, "train_episodes.csv"), "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader(); wr.writerows(rows)
    with open(os.path.join(a.out, "probes.json"), "w") as f:
        json.dump(probes, f, indent=1)


if __name__ == "__main__":
    main()
