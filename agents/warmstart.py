"""Runbook warm-start: load recorded JSONL transitions into a replay buffer and run the offline phase
(CLAUDE.md §5.11 warm-start mode, §5.12; PLAN.md M4; AWARE-style bootstrapping).

The same loader rebuilds the buffer on resume (§5.12 rule 10): every episode file of the given transition
directories is replayed through `NStepBuilder`, so stale s′ are dropped and n-step targets are rebuilt
exactly as online. Probe episodes (`source == "probe"`) are never loaded: they are greedy evaluations,
not training data.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from agents.masked_discrete_sac import MaskedDiscreteSAC, UpdateStats
from agents.per_buffer import NStepBuilder, ReplayBuffer, Step, Transition, beta_at
from env.contract import PerCfg

PROBE_SOURCE = "probe"


@dataclass(frozen=True)
class LoadStats:
    episodes: int
    steps: int
    transitions: int
    dropped_stale: int
    skipped_probe_steps: int


def step_from_record(r: dict) -> Step:
    return Step(np.asarray(r["obs"], np.float32), np.asarray(r["mask"], np.int8), int(r["a_exec"]),
                float(r["reward"]), np.asarray(r["next_obs"], np.float32), np.asarray(r["next_mask"], np.int8),
                bool(r["terminated"]), bool(r["truncated"]), bool(r["stale"]))


def episode_files(dirs: Iterable[Path]) -> Iterator[Path]:
    for d in dirs:
        yield from sorted(Path(d).glob("episode_*.jsonl"), key=lambda f: int(f.stem.split("_")[1]))


def load_transitions(dirs: Iterable[Path], gamma: float, n_step: int) -> tuple[list[Transition], LoadStats]:
    out: list[Transition] = []
    episodes = steps = skipped = 0
    nb = NStepBuilder(gamma, n_step)
    for f in episode_files(dirs):
        nb.reset()
        rows = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
        probe = [r for r in rows if r.get("source") == PROBE_SOURCE]
        skipped += len(probe)
        rows = [r for r in rows if "obs" in r and r.get("source") != PROBE_SOURCE]
        if not rows:
            continue
        episodes += 1
        for r in sorted(rows, key=lambda r: r["tick"]):
            steps += 1
            out += nb.push(step_from_record(r))
        out += nb.finish()
    return out, LoadStats(episodes, steps, len(out), nb.dropped_stale, skipped)


def fill(buffer: ReplayBuffer, transitions: Iterable[Transition]) -> None:
    for t in transitions:
        buffer.add(t)


def offline_pretrain(agent: MaskedDiscreteSAC, buffer: ReplayBuffer, steps: int, batch_size: int,
                     per: PerCfg) -> list[UpdateStats]:
    """The offline phase: `steps` gradient steps on the warm-start buffer (β advances with them)."""
    if len(buffer) < batch_size:
        raise ValueError(f"warm-start buffer has {len(buffer)} transitions < batch size {batch_size}")
    stats = []
    for _ in range(steps):
        b = buffer.sample(batch_size, beta_at(per, agent.grad_step))
        s, prio = agent.update(b)
        buffer.update_priorities(b.idx, prio)
        stats.append(s)
    return stats
