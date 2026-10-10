"""Online PER-iSAC training on the real-cluster env (CLAUDE.md §5.7, §5.12; PLAN.md M4). Mutates the cluster.

Per tick: act (stochastic, masked) → env.step → n-step transitions into the buffer (stale s′ never
stored) → up to `updates_per_tick` gradient steps, hard-capped at `update_budget_s`.

    warm start (default): fill the buffer from the runbook collection (--warmstart DIR ...), run the
                          `offline_warmstart_steps` offline phase, then go online.
    cold start (--cold):  empty buffer, no offline phase; updates begin once the buffer holds a batch.
    uniform (--uniform):  the replay ablation (same code, prioritization off).

Greedy probes (`--probe-every`, `--probe-episodes`): argmax episodes with no learning, tagged
source=probe, never loaded into the buffer; one at episode 0 (after the offline phase), then every N
completed training episodes.

Checkpoints: <out>/ckpt/latest.pt after every episode (crash safety) and ep<N>.pt every 25 episodes and
at the end; on SIGTERM/SIGINT the current tick finishes, the episode closes and latest.pt is written.
Resume (the watchdog reruns the same command): latest.pt is loaded and the buffer rebuilt from the
warm-start and this run's transition files, priorities reset to max (§5.12 rule 10). A checkpoint whose
contract/calibration/golden hashes differ from the current files is refused.

Kill criteria (PLAN.md M4) checked automatically: non-finite loss, α outside [1e-4, 1]. Either writes
<out>/KILLED with the reason and exits 0, so the watchdog does not restart it and a chain stops. Q-gap
and FRR are logged per episode for the human checks.

Usage:  source config/cluster.env
        python -m agents.train --run sac_per_s0 --episodes 200 --seed 0 --warmstart data/transitions/warmstart_runbook
        python -m agents.train --run sac_per_cold_s0 --episodes 200 --seed 0 --cold
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import FrameType
from typing import Any

import numpy as np
import torch

from agents.masked_discrete_sac import MaskedDiscreteSAC, NonFiniteLoss, UpdateStats
from agents.per_buffer import NStepBuilder, ReplayBuffer, Step, beta_at
from agents.run_policy import completed_episodes, last_episode_number
from agents.warmstart import PROBE_SOURCE, fill, load_transitions, offline_pretrain
from env.boutique_env import BoutiqueEnv, EnvironmentDegraded, make_env
from env.contract import (
    CALIBRATION_PATH,
    GOLDEN_LIVE_PATH,
    REPO_ROOT,
    ContractError,
    sha256_file,
)

CHECKPOINT_EVERY = 25                 # §5.12 rule 10
ALPHA_RANGE = (1e-4, 1.0)             # PLAN.md M4 kill criterion
RESUME_SEED_STRIDE = 10_007           # same reseeding rule as agents/run_policy.py

_stop = False


def _on_signal(signum: int, _frame: FrameType | None) -> None:
    global _stop
    _stop = True


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def file_hashes(contract_sha: str) -> dict[str, str]:
    return {"contract": contract_sha, "calibration": sha256_file(CALIBRATION_PATH),
            "golden_live": sha256_file(GOLDEN_LIVE_PATH)}


def pin_cpu(locust_cores: tuple[int, ...] | list[int], threads: int) -> None:
    """Torch off Locust's cores, with `torch_threads` threads (§8 pitfall 9)."""
    torch.set_num_threads(threads)
    cores = set(os.sched_getaffinity(0)) - set(locust_cores)
    if cores:
        os.sched_setaffinity(0, cores)


def q_minus_return(agent: MaskedDiscreteSAC, steps: list[Step], gamma: float) -> float | None:
    """Mean of min-Q(s_t, a_t) − realised discounted return G_t over a finished episode (overestimation)."""
    if not steps:
        return None
    g, returns = 0.0, []
    for s in reversed(steps):
        g = s.reward + gamma * g
        returns.append(g)
    returns.reverse()
    obs = torch.as_tensor(np.stack([s.obs for s in steps]))
    act = torch.as_tensor([s.action for s in steps]).unsqueeze(-1)
    with torch.no_grad():
        q1, q2 = agent.critic(obs)
        q = torch.min(q1, q2).gather(-1, act).squeeze(-1).numpy()
    return float(np.mean(q - np.asarray(returns)))


def _mean(stats: list[UpdateStats], field: str) -> float | None:
    return float(np.mean([getattr(s, field) for s in stats])) if stats else None


class Trainer:
    def __init__(self, args: argparse.Namespace, env: BoutiqueEnv) -> None:
        self.args, self.env = args, env
        c = env.c
        self.c = c
        self.out: Path = args.out
        self.ckpt_dir = self.out / "ckpt"
        self.ckpt_dir.mkdir(parents=True, exist_ok=True)
        self.agent = MaskedDiscreteSAC(c.rl, c.per, seed=args.seed)
        self.buffer = ReplayBuffer(c.rl.buffer_size, c.per, prioritized=not args.uniform, seed=args.seed)
        self.builder = NStepBuilder(c.rl.gamma, c.rl.n_step)
        self.hashes = file_hashes(c.sha256)
        self.train_done = completed_episodes(self.out / "episodes.jsonl")
        self.probes_done: list[int] = []        # training-episode counts at which a probe ran

    # ------------------------------------------------------------------------- checkpoints

    def save(self, name: str) -> None:
        sd: dict[str, Any] = {
            "agent": self.agent.state_dict(), "train_done": self.train_done, "probes_done": self.probes_done,
            "buffer_rng": self.buffer.rng.bit_generator.state, "py_rng": random.getstate(),
            "np_rng": np.random.get_state(), "torch_rng": torch.get_rng_state(), "hashes": self.hashes,
            "git_sha": _git_sha(), "args": {k: str(v) for k, v in vars(self.args).items()}, "t_wall": time.time()}
        tmp = self.ckpt_dir / f".{name}.tmp"
        torch.save(sd, tmp)
        os.replace(tmp, self.ckpt_dir / name)

    def warm_dirs(self) -> list[Path]:
        return [] if self.args.cold else list(self.args.warmstart)

    def rebuild_buffer(self) -> dict[str, Any]:
        own = self.env.transitions_root / self.env.run
        ts, st = load_transitions([*self.warm_dirs(), own], self.c.rl.gamma, self.c.rl.n_step)
        fill(self.buffer, ts)
        self.buffer.reset_priorities_to_max()
        return vars(st)

    def resume_or_start(self) -> None:
        latest = self.ckpt_dir / "latest.pt"
        if latest.exists():
            sd = torch.load(latest, weights_only=False)
            if sd["hashes"] != self.hashes:
                raise ContractError(f"checkpoint hashes {sd['hashes']} differ from current files {self.hashes}")
            self.agent.load_state_dict(sd["agent"])
            self.probes_done = list(sd["probes_done"])
            st = self.rebuild_buffer()
            self.buffer.rng.bit_generator.state = sd["buffer_rng"]
            self.log({"event": "resumed", "train_done": self.train_done, "ckpt_train_done": sd["train_done"],
                      "grad_step": self.agent.grad_step, "buffer": len(self.buffer), **st})
            return
        if self.args.cold:
            self.log({"event": "cold_start"})
        else:
            ts, st = load_transitions(self.warm_dirs(), self.c.rl.gamma, self.c.rl.n_step)
            fill(self.buffer, ts)
            t0 = time.monotonic()
            stats = offline_pretrain(self.agent, self.buffer, self.c.rl.offline_warmstart_steps,
                                     self.c.rl.batch_size, self.c.per)
            self.log({"event": "offline_warmstart", **vars(st), "buffer": len(self.buffer),
                      "grad_steps": self.agent.grad_step, "seconds": time.monotonic() - t0,
                      "critic_loss_last100": float(np.mean([s.critic_loss for s in stats[-100:]])),
                      "alpha": self.agent.alpha.item()})
            self.save("warm.pt")
        self.save("latest.pt")

    def log(self, row: dict[str, Any]) -> None:
        row = {"t_wall": time.time(), **row}
        with (self.out / "train_events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)

    # ------------------------------------------------------------------------- episodes

    def run_episode(self, *, probe: bool) -> dict[str, Any]:
        env, agent, rl = self.env, self.agent, self.c.rl
        env.source = PROBE_SOURCE if probe else "policy"
        obs, info = env.reset()
        self.builder.reset()
        ep: dict[str, Any] = {"run": env.run, "episode": info["episode"], "probe": probe, "fault": info["fault"],
                              "target": info["target"], "severity": info["severity"], "lead_in": info["lead_in"],
                              "reset_s": info["reset_s"], "t_start": time.time()}
        steps: list[Step] = []
        stats: list[UpdateStats] = []
        ticks = actions = stale = invalid = updates_capped = 0
        latencies, rewards, update_s = [], [], []
        terminated = truncated = False
        last: dict[str, Any] = {}
        while not (terminated or truncated):
            a = agent.act(obs["obs"], obs["mask"], greedy=probe)
            nxt, r, terminated, truncated, last = env.step(a)
            s = Step(obs["obs"], obs["mask"], int(last["a_exec"]), float(r), nxt["obs"], nxt["mask"],
                     bool(terminated), bool(truncated), bool(last["stale"]))
            steps.append(s)
            obs = nxt
            ticks += 1
            actions += int(last["a_exec"] != 0)
            stale += int(last["stale"])
            invalid += int(not last["valid"])
            latencies.append(last["decision_latency_s"])
            rewards.append(r)
            if not probe:
                for t in self.builder.push(s):
                    self.buffer.add(t)
                n_up, secs = self._updates(stats)
                update_s.append(secs)
                updates_capped += int(0 < n_up < rl.updates_per_tick)
            if _stop:
                break
        ep.update({
            "ticks": ticks, "actions": actions, "stale_ticks": stale, "invalid_ticks": invalid,
            "terminated": terminated, "truncated": truncated, "recovered": last.get("recovered"),
            "mttr_s": last.get("mttr_s"), "return": float(sum(rewards)), "interrupted": _stop,
            "decision_latency_p95_s": float(np.percentile(latencies, 95)) if latencies else None,
            "grad_step": agent.grad_step, "buffer": len(self.buffer),
            "beta": beta_at(self.c.per, agent.grad_step), "alpha": agent.alpha.item(),
            "updates": len(stats), "updates_capped_ticks": updates_capped,
            "update_s_max": max(update_s) if update_s else None,
            "critic_loss": _mean(stats, "critic_loss"), "actor_loss": _mean(stats, "actor_loss"),
            "entropy": _mean(stats, "entropy"), "q_mean": _mean(stats, "q_mean"), "q_gap": _mean(stats, "q_gap"),
            "alpha_excluded_frac": _mean(stats, "alpha_excluded_frac"),
            "per_max_priority": self.buffer.max_priority(),
            "q_minus_return": q_minus_return(agent, steps, rl.gamma), "dropped_stale": self.builder.dropped_stale})
        return ep

    def _updates(self, stats: list[UpdateStats]) -> tuple[int, float]:
        rl = self.c.rl
        if len(self.buffer) < rl.batch_size:
            return 0, 0.0
        t0 = time.monotonic()
        n = 0
        while n < rl.updates_per_tick and time.monotonic() - t0 < self.c.clock.update_budget_s:
            b = self.buffer.sample(rl.batch_size, beta_at(self.c.per, self.agent.grad_step))
            s, prio = self.agent.update(b)
            self.buffer.update_priorities(b.idx, prio)
            stats.append(s)
            n += 1
        return n, time.monotonic() - t0

    def kill(self, reason: str) -> int:
        (self.out / "KILLED").write_text(reason + "\n")
        self.log({"event": "killed", "reason": reason})
        return 0

    def probe_due(self) -> bool:
        a = self.args
        if a.probe_every <= 0 or a.probe_episodes <= 0:
            return False
        due = 0 if not self.probes_done else self.probes_done[-1] + a.probe_every
        return self.train_done >= due and self.train_done not in self.probes_done

    def run(self) -> int:
        a = self.args
        while self.train_done < a.episodes and not _stop:
            if self.probe_due():
                for _ in range(a.probe_episodes):
                    ep = self.run_episode(probe=True)
                    ep["after_train_episodes"] = self.train_done
                    with (self.out / "probes.jsonl").open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(ep) + "\n")
                    print(f"probe@{self.train_done} ep {ep['episode']}: {ep['fault']} actions={ep['actions']} "
                          f"recovered={ep['recovered']} mttr={ep['mttr_s']}", flush=True)
                    if _stop:
                        break
                if _stop:
                    break
                self.probes_done.append(self.train_done)
                self.save("latest.pt")
            ep = self.run_episode(probe=False)
            with (self.out / "episodes.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(ep) + "\n")
            if not ep["interrupted"]:
                self.train_done += 1
            print(f"episode {ep['episode']} [{self.train_done}/{a.episodes}]: {ep['fault']} {ep['target'] or ''} "
                  f"actions={ep['actions']} recovered={ep['recovered']} mttr={ep['mttr_s']} "
                  f"return={ep['return']:.2f} alpha={ep['alpha']:.4f} buffer={ep['buffer']}", flush=True)
            self.save("latest.pt")
            if self.train_done % CHECKPOINT_EVERY == 0 or self.train_done == a.episodes:
                self.save(f"ep{self.train_done}.pt")
            if self.agent.grad_step and not ALPHA_RANGE[0] <= ep["alpha"] <= ALPHA_RANGE[1]:
                return self.kill(f"alpha {ep['alpha']:.3g} outside {ALPHA_RANGE} after {self.train_done} episodes")
        if self.train_done >= a.episodes:
            self.save("final.pt")
            self.log({"event": "done", "train_done": self.train_done, "grad_step": self.agent.grad_step})
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="run name (transitions go to data/transitions/<run>)")
    ap.add_argument("--episodes", type=int, required=True, help="TOTAL training episodes (resume runs the rest)")
    ap.add_argument("--seed", type=int, default=0)
    start = ap.add_mutually_exclusive_group(required=True)
    start.add_argument("--warmstart", type=Path, nargs="+", help="runbook transition dir(s) for the warm start")
    start.add_argument("--cold", action="store_true", help="cold start: empty buffer, no offline phase")
    ap.add_argument("--uniform", action="store_true", help="uniform replay ablation (PER off)")
    ap.add_argument("--probe-every", type=int, default=75)
    ap.add_argument("--probe-episodes", type=int, default=5)
    ap.add_argument("--out", type=Path, default=None, help="default data/m4/<run>")
    args = ap.parse_args(argv)
    args.out = args.out or REPO_ROOT / "data" / "m4" / args.run
    args.out.mkdir(parents=True, exist_ok=True)
    if (args.out / "KILLED").exists():
        print(f"train: {args.out}/KILLED exists ({(args.out / 'KILLED').read_text().strip()}); not running")
        return 0
    if args.warmstart:
        missing = [d for d in args.warmstart if not d.is_dir()]
        if missing:
            print(f"train: warm-start dir(s) missing: {missing}", file=sys.stderr)
            return 2

    done = completed_episodes(args.out / "episodes.jsonl")
    seed = args.seed + RESUME_SEED_STRIDE * done
    random.seed(seed)
    np.random.seed(seed)
    try:
        env = make_env(args.run, seed)
    except (ContractError, RuntimeError) as exc:
        print(f"train: {exc}", file=sys.stderr)
        return 2
    pin_cpu(env.c.locust.cpu_cores, env.c.rl.torch_threads)
    torch.manual_seed(args.seed)
    env.episode_count = last_episode_number(args.out / "episodes.jsonl", env.transitions_root / args.run)
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    trainer = Trainer(args, env)
    try:
        trainer.resume_or_start()
        rc = trainer.run()
    except NonFiniteLoss as exc:
        rc = trainer.kill(f"non-finite loss: {exc}")
    except EnvironmentDegraded as exc:
        print(f"train: environment degraded: {exc}", file=sys.stderr)
        trainer.save("latest.pt")
        env.close()
        return 3
    except ContractError as exc:
        print(f"train: {exc}", file=sys.stderr)
        env.close()
        return 2
    trainer.save("latest.pt")
    env.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
