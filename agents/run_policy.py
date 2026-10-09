"""Run a fixed policy on the real-cluster env for N episodes (PLAN.md M3). Mutates the cluster.

Policies: noop | random (uniform over valid actions) | runbook (CLAUDE.md §5.11, optional ε).
Writes one summary line per episode to <out>/episodes.jsonl; transitions go to
data/transitions/<run>/ and events to data/logs/<run>.events.jsonl. SIGTERM/SIGINT finish the
current tick, close the episode and exit 0 (§6). Requires a fresh calibration.json (§7).

Usage:  source config/cluster.env
        python -m agents.run_policy --policy random --episodes 3 --no-faults --out data/m3/contract
        python -m agents.run_policy --policy runbook --episodes 40 --epsilon 0 --out data/m3/runbook_eval
"""

from __future__ import annotations

import argparse
import json
import random
import signal
import statistics
import sys
import time
from pathlib import Path
from types import FrameType
from typing import Any

import numpy as np

from agents.runbook import Runbook
from env.boutique_env import EnvironmentDegraded, make_env
from env.contract import ContractError

_stop = False


def _on_signal(signum: int, _frame: FrameType | None) -> None:
    global _stop
    _stop = True


def choose(policy: str, rng: random.Random, runbook: Runbook | None, obs: dict[str, np.ndarray]
           ) -> tuple[int, str]:
    mask = obs["mask"]
    if policy == "noop":
        return 0, "policy"
    if policy == "random":
        return rng.choice([i for i in range(len(mask)) if mask[i]]), "policy"
    assert runbook is not None
    aid, _rule, source = runbook.act(obs["obs"], mask)
    return aid, source


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", choices=["noop", "random", "runbook"], required=True)
    ap.add_argument("--episodes", type=int, required=True)
    ap.add_argument("--epsilon", type=float, default=0.0, help="runbook only: warm-start exploration")
    ap.add_argument("--no-faults", action="store_true", help="contract check: NULL episodes only")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--run", default=None, help="run name (default: <out dir name>)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    run = args.run or args.out.name

    random.seed(args.seed)
    np.random.seed(args.seed)
    rng = random.Random(args.seed)
    try:
        env = make_env(run, args.seed, faults_enabled=not args.no_faults)
    except (ContractError, RuntimeError) as exc:
        print(f"run_policy: {exc}", file=sys.stderr)
        return 2
    runbook = Runbook(env.c, env.cal, epsilon=args.epsilon, rng=random.Random(args.seed + 1)) \
        if args.policy == "runbook" else None
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    args.out.mkdir(parents=True, exist_ok=True)
    summary_path = args.out / "episodes.jsonl"

    try:
        for _ in range(args.episodes):
            if _stop:
                break
            obs, info = env.reset()
            if runbook is not None:
                runbook.reset()
            ep: dict[str, Any] = {"run": run, "episode": info["episode"], "fault": info["fault"],
                                  "target": info["target"], "severity": info["severity"], "lead_in": info["lead_in"],
                                  "reset_s": info["reset_s"], "t_start": time.time()}
            ticks, actions, stale, invalid, latencies, rewards = 0, 0, 0, 0, [], []
            terminated = truncated = False
            last: dict[str, Any] = {}
            while not (terminated or truncated):
                a, env.source = choose(args.policy, rng, runbook, obs)
                obs, r, terminated, truncated, last = env.step(a)
                ticks += 1
                actions += int(last["a_exec"] != 0)
                stale += int(last["stale"])
                invalid += int(not last["valid"])
                latencies.append(last["decision_latency_s"])
                rewards.append(r)
                if _stop:                       # finish this tick, then end the episode early
                    break
            ep.update({"ticks": ticks, "actions": actions, "stale_ticks": stale, "invalid_ticks": invalid,
                       "terminated": terminated, "truncated": truncated, "recovered": last.get("recovered"),
                       "mttr_s": last.get("mttr_s"), "return": sum(rewards),
                       "decision_latency_p95_s": sorted(latencies)[max(0, round(0.95 * len(latencies)) - 1)]
                       if latencies else None, "interrupted": _stop})
            with summary_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(ep) + "\n")
            print(f"episode {ep['episode']}: {ep['fault']} {ep['target'] or ''} ticks={ticks} actions={actions} "
                  f"stale={stale} recovered={ep['recovered']} mttr={ep['mttr_s']}", flush=True)
    except EnvironmentDegraded as exc:
        print(f"run_policy: environment degraded: {exc}", file=sys.stderr)
        env.close()
        return 3
    env.close()
    lat = [json.loads(line)["decision_latency_p95_s"] for line in summary_path.read_text().splitlines()]
    print(f"done; episode summaries in {summary_path} (median p95 decision latency "
          f"{statistics.median([x for x in lat if x is not None]) if lat else float('nan'):.2f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
