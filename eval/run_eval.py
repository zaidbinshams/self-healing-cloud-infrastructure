"""Interleaved evaluation on the committed schedule (docs/PREREGISTRATION.md §2–3, §6). Mutates the cluster.

For each schedule entry i (in schedule order), every policy runs entry i once, in a per-entry random
policy order (seeded), before entry i+1 starts. So drift in the cluster or the network over the days
of evaluation hits all policies alike. An episode with valid = False is re-run on the same entry (up to
--max-attempts) and the exclusion is recorded. Resumable: a rerun skips (entry, policy) pairs that
already have a valid row in <out>/episodes.jsonl.

Policies:
    k8s_default    NOOP every tick (Kubernetes' own controllers only)
    k8s_hpa        NOOP + the HPA baseline objects (requires env HPA support; refused otherwise)
    runbook        scripted runbook, ε = 0
    sac:<ckpt>     masked discrete SAC, greedy (argmax over masked logits), from a checkpoint

Refuses a dirty tracked tree (CLAUDE.md §4.4.3) and records the git SHA and file hashes in
<out>/manifest.json; a resume with different hashes is refused.

Usage:  source config/cluster.env
        python -m eval.run_eval --schedule eval/schedules/eval_v1.json --out data/eval/main \\
            --policy k8s_default --policy k8s_hpa --policy runbook \\
            --policy sac_per=data/m4/sac_per_s0/ckpt/final.pt --policy sac_per_cold=data/m4/sac_per_cold_s0/ckpt/final.pt
        ... --subset edge --out data/eval/edge --policy runbook --policy sac_per=...
"""

from __future__ import annotations

import argparse
import json
import random
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import Any

import numpy as np
import torch

from agents.masked_discrete_sac import MaskedDiscreteSAC
from agents.runbook import Runbook
from agents.train import file_hashes
from env.boutique_env import BoutiqueEnv, EnvironmentDegraded, make_env
from env.contract import REPO_ROOT, ContractError
from env.injector import plan_from_schedule
from eval.metrics import episode_metrics

_stop = False


def _on_signal(signum: int, _frame: FrameType | None) -> None:
    global _stop
    _stop = True


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()


def policy_order(names: list[str], index: int, seed: int) -> list[str]:
    """Per-entry random order of the policies (pure, reproducible from (seed, index))."""
    order = list(names)
    random.Random(seed * 100_003 + index).shuffle(order)
    return order


def done_pairs(rows: list[dict[str, Any]]) -> set[tuple[int, str]]:
    return {(int(r["index"]), str(r["policy"])) for r in rows if r.get("valid") and not r.get("interrupted")}


class Policy:
    def __init__(self, name: str, act: Callable[[dict[str, np.ndarray]], int], reset: Callable[[], None],
                 hpa: bool = False) -> None:
        self.name, self.act, self.reset, self.hpa = name, act, reset, hpa


def build_policy(spec: str, env: BoutiqueEnv) -> Policy:
    name, _, ckpt = spec.partition("=")
    if name == "k8s_default":
        return Policy(name, lambda o: 0, lambda: None)
    if name == "k8s_hpa":
        if not getattr(env, "supports_hpa_baseline", False):
            raise ContractError("k8s_hpa needs env HPA-baseline support (not available); refusing to run it as NOOP")
        return Policy(name, lambda o: 0, lambda: None, hpa=True)
    if name == "runbook":
        rb = Runbook(env.c, env.cal, epsilon=0.0, rng=random.Random(0))
        return Policy(name, lambda o: rb.act(o["obs"], o["mask"])[0], rb.reset)
    if ckpt:
        agent = MaskedDiscreteSAC(env.c.rl, env.c.per)
        sd = torch.load(ckpt, weights_only=False)
        if sd["hashes"] != file_hashes(env.c.sha256):
            raise ContractError(f"{ckpt}: checkpoint hashes differ from the current contract/calibration/golden")
        agent.load_state_dict(sd["agent"])
        return Policy(name, lambda o: agent.act(o["obs"], o["mask"], greedy=True), lambda: None)
    raise ValueError(f"unknown policy spec {spec!r}")


def run_one(env: BoutiqueEnv, pol: Policy, entry: dict[str, Any]) -> dict[str, Any]:
    env.source = pol.name
    options: dict[str, Any] = {"plan": plan_from_schedule(entry)}
    if pol.hpa:
        options["hpa_baseline"] = True
    obs, info = env.reset(options=options)
    pol.reset()
    terminated = truncated = False
    last: dict[str, Any] = {}
    while not (terminated or truncated):
        obs, _r, terminated, truncated, last = env.step(pol.act(obs))
        if _stop:
            break
    assert env.recorder is not None                    # flushed every tick (CLAUDE.md §6)
    records = [json.loads(x) for x in env.recorder.path.read_text().splitlines() if x.strip()]
    m = episode_metrics(records, last, pol.name, int(entry["index"]), env.cal, env.c)
    return {**m.row(), "episode": info["episode"], "reset_s": info["reset_s"], "interrupted": _stop,
            "t_end": time.time()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schedule", type=Path, required=True)
    ap.add_argument("--policy", action="append", required=True, help="name or name=checkpoint (repeat)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--subset", choices=["main", "edge"], default="main")
    ap.add_argument("--order-seed", type=int, default=None, help="default: the schedule seed")
    ap.add_argument("--max-attempts", type=int, default=3)
    args = ap.parse_args(argv)
    if git("status", "--porcelain", "--untracked-files=no"):
        print("run_eval: refusing: tracked working tree is dirty (CLAUDE.md §4.4.3)", file=sys.stderr)
        return 2
    sched = json.loads(args.schedule.read_text())
    entries = sched["entries"]
    if args.subset == "edge":
        keep = set(sched["edge_subset"])
        entries = [e for e in entries if e["index"] in keep]
    seed = sched["seed"] if args.order_seed is None else args.order_seed
    run = args.out.name
    try:
        env = make_env(run, seed)
    except (ContractError, RuntimeError) as exc:
        print(f"run_eval: {exc}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = {"git_sha": git("rev-parse", "HEAD"), "hashes": file_hashes(env.c.sha256), "schedule": str(args.schedule),
                "subset": args.subset, "policies": args.policy, "order_seed": seed}
    mpath = args.out / "manifest.json"
    if mpath.exists():
        old = json.loads(mpath.read_text())
        if old["hashes"] != manifest["hashes"] or old["policies"] != manifest["policies"]:
            print("run_eval: refusing to resume: hashes or policies differ from manifest.json", file=sys.stderr)
            return 2
    else:
        mpath.write_text(json.dumps(manifest, indent=1))
    try:
        policies = {p.name: p for p in (build_policy(s, env) for s in args.policy)}
    except (ContractError, ValueError, OSError) as exc:
        print(f"run_eval: {exc}", file=sys.stderr)
        env.close()
        return 2
    rows_path = args.out / "episodes.jsonl"
    rows = [json.loads(x) for x in rows_path.read_text().splitlines()] if rows_path.exists() else []
    done = done_pairs(rows)
    env.episode_count = max([int(r["episode"]) for r in rows] + [0])
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    try:
        for entry in entries:
            for name in policy_order(list(policies), int(entry["index"]), seed):
                if (int(entry["index"]), name) in done:
                    continue
                for attempt in range(1, args.max_attempts + 1):
                    row = {**run_one(env, policies[name], entry), "attempt": attempt}
                    with rows_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(row) + "\n")
                    print(f"[{entry['index']:02d}] {name:14s} {row['fault']:4s} valid={row['valid']} "
                          f"recovered={row['recovered']} mttr={row['mttr_s']} actions={row['actions']}", flush=True)
                    if row["valid"] or _stop:
                        break
                if _stop:
                    env.close()
                    return 0
    except EnvironmentDegraded as exc:
        print(f"run_eval: environment degraded: {exc}", file=sys.stderr)
        env.close()
        return 3
    env.close()
    print(f"run_eval: done; rows in {rows_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
