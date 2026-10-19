"""Masked discrete Soft Actor-Critic (CLAUDE.md §5.12; in-house, human-approved 2026-10-10).

Discrete SAC (Christodoulou, 2019) with action masks. Every rule of §5.12 is implemented by one small
function below so it can be unit-tested on hand-written tensors:

    rule 1  masked_policy        logits masked to -1e9; log-probs of masked actions zeroed before π·logπ
    rule 2  soft_value, td_target V(s′) = Σ_valid π(a|s′)[min Q̄(s′,a) − α log π(a|s′)];  y = R + (1−done) γ^k V(s′)
    rule 3  critic_loss          IS-weighted squared TD error (IS weights on the critic only)
    rule 4  actor_loss           E_s Σ_valid π(a|s)(α log π(a|s) − min Q(s,a))
    rule 5  alpha_loss           per-state target entropy frac·log n_valid(s); states with n_valid < 2 excluded
    rule 6  priorities           via agents.per_buffer.priority_from_td
    rule 9  act(greedy=True)     argmax over masked logits

`MaskedDiscreteSAC.update` performs one gradient step (critics, actor, α, then Polyak) and raises
`NonFiniteLoss` instead of stepping on a NaN/inf loss (§8 pitfall 11).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

import numpy as np
import torch
from torch import nn

from agents.per_buffer import Batch, priority_from_td
from env.contract import N_ACTIONS, OBS_DIM, PerCfg, RlCfg

MASK_FILL = -1e9                  # §5.12 rule 1


class NonFiniteLoss(RuntimeError):
    """A loss became NaN/inf; the update is not applied (kill criterion: divergence)."""


def mlp(in_dim: int, hidden: tuple[int, ...], out_dim: int) -> nn.Sequential:
    layers: list[nn.Module] = []
    d = in_dim
    for h in hidden:
        layers += [nn.Linear(d, h), nn.ReLU()]
        d = h
    layers.append(nn.Linear(d, out_dim))
    return nn.Sequential(*layers)


class TwinQ(nn.Module):
    def __init__(self, hidden: tuple[int, ...]) -> None:
        super().__init__()
        self.q1, self.q2 = mlp(OBS_DIM, hidden, N_ACTIONS), mlp(OBS_DIM, hidden, N_ACTIONS)

    def forward(self, obs: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        return self.q1(obs), self.q2(obs)


# ----------------------------------------------------------------------------- pure functions

def masked_policy(logits: torch.Tensor, mask: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """(probs, log_probs); both exactly 0 at masked entries, so π·logπ never forms 0·(−inf)."""
    log_probs = torch.log_softmax(torch.where(mask, logits, torch.full_like(logits, MASK_FILL)), dim=-1)
    log_probs = torch.where(mask, log_probs, torch.zeros_like(log_probs))
    probs = torch.where(mask, log_probs.exp(), torch.zeros_like(log_probs))
    return probs, log_probs


def entropy(probs: torch.Tensor, log_probs: torch.Tensor) -> torch.Tensor:
    return -(probs * log_probs).sum(-1)


def soft_value(probs: torch.Tensor, log_probs: torch.Tensor, q_min: torch.Tensor, mask: torch.Tensor,
               alpha: torch.Tensor | float) -> torch.Tensor:
    """V(s) = Σ_{a valid} π(a|s)·[min Q(s,a) − α log π(a|s)]."""
    q = torch.where(mask, q_min, torch.zeros_like(q_min))
    return (probs * (q - alpha * log_probs)).sum(-1)


def td_target(ret: torch.Tensor, done: torch.Tensor, discount: torch.Tensor, v_next: torch.Tensor) -> torch.Tensor:
    """y = R^(k) + (1 − done)·γ^k·V(s′); done = terminated only (truncation bootstraps, §5.6)."""
    return ret + (1.0 - done) * discount * v_next


def critic_loss(q1_a: torch.Tensor, q2_a: torch.Tensor, y: torch.Tensor, weight: torch.Tensor
                ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    td1, td2 = q1_a - y, q2_a - y
    loss = (weight * td1.pow(2)).mean() + (weight * td2.pow(2)).mean()
    return loss, td1.detach(), td2.detach()


def actor_loss(probs: torch.Tensor, log_probs: torch.Tensor, q_min: torch.Tensor, mask: torch.Tensor,
               alpha: torch.Tensor | float) -> torch.Tensor:
    q = torch.where(mask, q_min, torch.zeros_like(q_min))
    return (probs * (alpha * log_probs - q)).sum(-1).mean()


def alpha_loss(log_alpha: torch.Tensor, ent: torch.Tensor, mask: torch.Tensor, target_entropy_frac: float
               ) -> tuple[torch.Tensor, float]:
    """(loss, fraction of states excluded). Target H̄(s) = frac·log n_valid(s); n_valid < 2 excluded."""
    n_valid = mask.sum(-1)
    keep = n_valid >= 2
    excluded = 1.0 - keep.float().mean().item()
    if not bool(keep.any()):
        return log_alpha * 0.0, excluded
    target = target_entropy_frac * torch.log(n_valid[keep].float())
    return (log_alpha.exp() * (ent[keep] - target).detach()).mean(), excluded


def cql_penalty(q: torch.Tensor, q_a: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """Discrete CQL regularizer (Kumar et al., 2020): mean[ logsumexp_{a valid} Q(s,a) − Q(s, a_data) ].

    Pushes down values of actions the data rarely shows (where Q overestimates), and up the data's action.
    Offline warm start (Amendment 5) and, with the same weight, every online update (Amendment 6)."""
    lse = torch.logsumexp(torch.where(mask, q, torch.full_like(q, MASK_FILL)), dim=-1)
    return (lse - q_a).mean()


def bc_loss(logits: torch.Tensor, mask: torch.Tensor, actions: torch.Tensor) -> torch.Tensor:
    """Behaviour cloning: cross-entropy of the masked policy at the data's action."""
    _, log_probs = masked_policy(logits, mask)
    return -log_probs.gather(-1, actions.unsqueeze(-1)).squeeze(-1).mean()


# ----------------------------------------------------------------------------- agent

@dataclass(frozen=True)
class UpdateStats:
    critic_loss: float
    actor_loss: float
    alpha_loss: float
    alpha: float
    entropy: float
    q_mean: float
    q_gap: float                   # mean |Q1 − Q2| at the taken action
    td_abs_mean: float
    alpha_excluded_frac: float


class MaskedDiscreteSAC:
    def __init__(self, rl: RlCfg, per: PerCfg, seed: int = 0) -> None:
        self.rl, self.per = rl, per
        torch.manual_seed(seed)
        hidden = tuple(rl.hidden)
        self.actor = mlp(OBS_DIM, hidden, N_ACTIONS)
        self.critic = TwinQ(hidden)
        self.critic_target = copy.deepcopy(self.critic).requires_grad_(False)
        self.log_alpha = torch.tensor(float(np.log(rl.alpha_init)), requires_grad=True)
        self.opt_actor = torch.optim.Adam(self.actor.parameters(), lr=rl.lr_actor)
        self.opt_critic = torch.optim.Adam(self.critic.parameters(), lr=rl.lr_critic)
        self.opt_alpha = torch.optim.Adam([self.log_alpha], lr=rl.lr_alpha)
        self.grad_step = 0
        self.act_rng = torch.Generator().manual_seed(seed + 1)

    @property
    def alpha(self) -> torch.Tensor:
        return self.log_alpha.exp()

    @torch.no_grad()
    def act(self, obs: np.ndarray, mask: np.ndarray, *, greedy: bool) -> int:
        o = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0)
        m = torch.as_tensor(mask, dtype=torch.bool).unsqueeze(0)
        if not bool(m.any()):
            raise ValueError("mask has no valid action (NOOP must always be valid, §5.8)")
        logits = self.actor(o)
        if greedy:
            return int(torch.where(m, logits, torch.full_like(logits, MASK_FILL)).argmax(-1).item())
        probs, _ = masked_policy(logits, m)
        return int(torch.multinomial(probs[0], 1, generator=self.act_rng).item())

    def update(self, b: Batch, cql_weight: float = 0.0, learn_alpha: bool = True) -> tuple[UpdateStats, np.ndarray]:
        """One gradient step on a sampled batch. Returns stats and the new priorities for b.idx.

        Amendment 6 (online phase): cql_weight > 0 adds the CQL penalty to the critic loss, and learn_alpha=False
        keeps α at its current value (alpha_init; the α loss is still computed and logged). Priorities stay |TD|."""
        obs = torch.as_tensor(b.obs)
        mask = torch.as_tensor(b.mask, dtype=torch.bool)
        act = torch.as_tensor(b.action, dtype=torch.int64).unsqueeze(-1)
        ret, done = torch.as_tensor(b.ret), torch.as_tensor(b.done)
        discount, weight = torch.as_tensor(b.discount), torch.as_tensor(b.weight)
        next_obs = torch.as_tensor(b.next_obs)
        next_mask = torch.as_tensor(b.next_mask, dtype=torch.bool)
        alpha = self.alpha.detach()

        with torch.no_grad():
            p_next, lp_next = masked_policy(self.actor(next_obs), next_mask)
            q1t, q2t = self.critic_target(next_obs)
            y = td_target(ret, done, discount, soft_value(p_next, lp_next, torch.min(q1t, q2t), next_mask, alpha))

        q1, q2 = self.critic(obs)
        q1_a, q2_a = q1.gather(-1, act).squeeze(-1), q2.gather(-1, act).squeeze(-1)
        c_loss, td1, td2 = critic_loss(q1_a, q2_a, y, weight)
        if cql_weight > 0.0:
            c_loss = c_loss + cql_weight * (cql_penalty(q1, q1_a, mask) + cql_penalty(q2, q2_a, mask))
        self._check(c_loss, "critic")

        probs, log_probs = masked_policy(self.actor(obs), mask)
        a_loss = actor_loss(probs, log_probs, torch.min(q1, q2).detach(), mask, alpha)
        self._check(a_loss, "actor")
        ent = entropy(probs, log_probs).detach()
        al_loss, excluded = alpha_loss(self.log_alpha, ent, mask, self.rl.target_entropy_frac)
        self._check(al_loss, "alpha")

        self.opt_critic.zero_grad()
        c_loss.backward()
        self.opt_critic.step()
        self.opt_actor.zero_grad()
        a_loss.backward()
        self.opt_actor.step()
        if learn_alpha:
            self.opt_alpha.zero_grad()
            al_loss.backward()
            self.opt_alpha.step()
        self._polyak()
        self.grad_step += 1

        prio = priority_from_td(td1.numpy(), td2.numpy(), self.per)
        stats = UpdateStats(c_loss.item(), a_loss.item(), al_loss.item(), self.alpha.item(), ent.mean().item(),
                            q1_a.detach().mean().item(), (q1_a - q2_a).abs().mean().item(),
                            0.5 * (td1.abs().mean() + td2.abs().mean()).item(), excluded)
        return stats, prio

    def offline_update(self, b: Batch, cql_weight: float) -> tuple[UpdateStats, np.ndarray]:
        """Offline warm-start step (Amendment 5): critic = SAC TD loss + cql_weight·CQL penalty; actor = behaviour
        cloning of the data's actions; α is not updated. The online phase uses `update` with the same CQL weight and α fixed."""
        obs = torch.as_tensor(b.obs)
        mask = torch.as_tensor(b.mask, dtype=torch.bool)
        act = torch.as_tensor(b.action, dtype=torch.int64)
        ret, done = torch.as_tensor(b.ret), torch.as_tensor(b.done)
        discount, weight = torch.as_tensor(b.discount), torch.as_tensor(b.weight)
        next_obs = torch.as_tensor(b.next_obs)
        next_mask = torch.as_tensor(b.next_mask, dtype=torch.bool)
        alpha = self.alpha.detach()
        with torch.no_grad():
            p_next, lp_next = masked_policy(self.actor(next_obs), next_mask)
            q1t, q2t = self.critic_target(next_obs)
            y = td_target(ret, done, discount, soft_value(p_next, lp_next, torch.min(q1t, q2t), next_mask, alpha))
        q1, q2 = self.critic(obs)
        q1_a, q2_a = q1.gather(-1, act.unsqueeze(-1)).squeeze(-1), q2.gather(-1, act.unsqueeze(-1)).squeeze(-1)
        td_loss, td1, td2 = critic_loss(q1_a, q2_a, y, weight)
        c_loss = td_loss + cql_weight * (cql_penalty(q1, q1_a, mask) + cql_penalty(q2, q2_a, mask))
        self._check(c_loss, "critic")
        logits = self.actor(obs)
        a_loss = bc_loss(logits, mask, act)
        self._check(a_loss, "actor")
        self.opt_critic.zero_grad()
        c_loss.backward()
        self.opt_critic.step()
        self.opt_actor.zero_grad()
        a_loss.backward()
        self.opt_actor.step()
        self._polyak()
        self.grad_step += 1
        with torch.no_grad():
            probs, log_probs = masked_policy(logits, mask)
            ent = entropy(probs, log_probs)
        prio = priority_from_td(td1.numpy(), td2.numpy(), self.per)
        stats = UpdateStats(c_loss.item(), a_loss.item(), 0.0, self.alpha.item(), ent.mean().item(),
                            q1_a.detach().mean().item(), (q1_a - q2_a).abs().mean().item(),
                            0.5 * (td1.abs().mean() + td2.abs().mean()).item(), 0.0)
        return stats, prio

    @staticmethod
    def _check(loss: torch.Tensor, name: str) -> None:
        if not torch.isfinite(loss):
            raise NonFiniteLoss(f"{name} loss is {loss.item()}")

    @torch.no_grad()
    def _polyak(self) -> None:
        tau = self.rl.tau
        for p, pt in zip(self.critic.parameters(), self.critic_target.parameters(), strict=True):
            pt.mul_(1.0 - tau).add_(p, alpha=tau)

    # ------------------------------------------------------------------------- checkpointing (§5.12 rule 10)

    def state_dict(self) -> dict[str, Any]:
        return {"actor": self.actor.state_dict(), "critic": self.critic.state_dict(),
                "critic_target": self.critic_target.state_dict(), "log_alpha": self.log_alpha.detach().clone(),
                "opt_actor": self.opt_actor.state_dict(), "opt_critic": self.opt_critic.state_dict(),
                "opt_alpha": self.opt_alpha.state_dict(), "grad_step": self.grad_step,
                "act_rng": self.act_rng.get_state()}

    def load_state_dict(self, sd: dict[str, Any]) -> None:
        self.actor.load_state_dict(sd["actor"])
        self.critic.load_state_dict(sd["critic"])
        self.critic_target.load_state_dict(sd["critic_target"])
        with torch.no_grad():
            self.log_alpha.copy_(sd["log_alpha"])
        self.opt_actor.load_state_dict(sd["opt_actor"])
        self.opt_critic.load_state_dict(sd["opt_critic"])
        self.opt_alpha.load_state_dict(sd["opt_alpha"])
        self.grad_step = int(sd["grad_step"])
        self.act_rng.set_state(sd["act_rng"])
