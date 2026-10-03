"""Masked discrete Soft Actor-Critic (CLAUDE.md §5.12 rules 1–5, 9).

Prototype note: this is a plain-PyTorch implementation. The production plan
uses Tianshou (rule 11); an in-house SAC needs human approval, so treat this
file as the reference for the maths and the unit tests, not as the M4 code.
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from .contract import CONTRACT, N_ACTIONS, OBS_DIM

RL = CONTRACT["rl"]
NEG = -1e9


def mlp(inp: int, out: int, hidden: list[int]) -> nn.Sequential:
    layers, d = [], inp
    for h in hidden:
        layers += [nn.Linear(d, h), nn.ReLU()]
        d = h
    layers.append(nn.Linear(d, out))
    return nn.Sequential(*layers)


def masked_policy(logits: torch.Tensor, mask: torch.Tensor):
    """Returns probs and log-probs with masked entries' log-probs zeroed (rule 1)."""
    z = torch.where(mask, logits, torch.full_like(logits, NEG))
    logp = F.log_softmax(z, dim=-1)
    p = logp.exp() * mask
    logp_z = torch.where(mask, logp, torch.zeros_like(logp))
    return p, logp_z, z


class MaskedDiscreteSAC:
    def __init__(self, seed: int):
        torch.manual_seed(seed)
        torch.set_num_threads(RL["torch_threads"])
        h = RL["hidden"]
        self.actor = mlp(OBS_DIM, N_ACTIONS, h)
        self.q1, self.q2 = mlp(OBS_DIM, N_ACTIONS, h), mlp(OBS_DIM, N_ACTIONS, h)
        self.q1t, self.q2t = mlp(OBS_DIM, N_ACTIONS, h), mlp(OBS_DIM, N_ACTIONS, h)
        self.q1t.load_state_dict(self.q1.state_dict())
        self.q2t.load_state_dict(self.q2.state_dict())
        self.log_alpha = torch.tensor(np.log(RL["alpha_init"]), requires_grad=True)
        self.opt_pi = torch.optim.Adam(self.actor.parameters(), lr=RL["lr_actor"])
        self.opt_q = torch.optim.Adam(list(self.q1.parameters()) + list(self.q2.parameters()),
                                      lr=RL["lr_critic"])
        self.opt_a = torch.optim.Adam([self.log_alpha], lr=RL["lr_alpha"])
        self.gen = torch.Generator().manual_seed(seed)
        self.grad_step = 0

    @property
    def alpha(self) -> float:
        return float(self.log_alpha.exp())

    # --------------------------------------------------------------- acting
    @torch.no_grad()
    def act(self, obs: np.ndarray, mask: np.ndarray, greedy: bool) -> int:
        s = torch.as_tensor(obs, dtype=torch.float32)[None]
        m = torch.as_tensor(mask.astype(bool))[None]
        p, _, z = masked_policy(self.actor(s), m)
        if greedy:
            return int(z.argmax(-1))                     # rule 9
        return int(torch.multinomial(p, 1, generator=self.gen))

    # -------------------------------------------------------------- learning
    def update(self, b: dict, w: np.ndarray) -> dict:
        s = torch.as_tensor(b["s"]); m = torch.as_tensor(b["m"])
        a = torch.as_tensor(b["a"]); R = torch.as_tensor(b["R"])
        s2 = torch.as_tensor(b["s2"]); m2 = torch.as_tensor(b["m2"])
        term = torch.as_tensor(b["term"]); gn = torch.as_tensor(b["gn"])
        w_t = torch.as_tensor(w)
        alpha = self.log_alpha.exp().detach()

        # critic target (rule 2): soft V over valid next actions, target critics
        with torch.no_grad():
            p2, logp2, _ = masked_policy(self.actor(s2), m2)
            q2min = torch.min(self.q1t(s2), self.q2t(s2))
            q2min = torch.where(m2, q2min, torch.zeros_like(q2min))
            v2 = (p2 * (q2min - alpha * logp2)).sum(-1)
            y = R + gn * (1.0 - term) * v2

        q1 = self.q1(s).gather(1, a[:, None]).squeeze(1)
        q2 = self.q2(s).gather(1, a[:, None]).squeeze(1)
        d1, d2 = q1 - y, q2 - y
        loss_q = (w_t * (d1.pow(2) + d2.pow(2))).mean() * 0.5   # rule 3: IS weights here only
        self.opt_q.zero_grad(); loss_q.backward(); self.opt_q.step()

        # actor (rule 4)
        p, logp, _ = masked_policy(self.actor(s), m)
        with torch.no_grad():
            qmin = torch.min(self.q1(s), self.q2(s))
            qmin = torch.where(m, qmin, torch.zeros_like(qmin))
        loss_pi = (p * (alpha * logp - qmin)).sum(-1).mean()
        self.opt_pi.zero_grad(); loss_pi.backward(); self.opt_pi.step()

        # temperature (rule 5): per-state target entropy, exclude n_valid == 1
        n_valid = m.sum(-1).float()
        ent = -(p * logp).sum(-1).detach()
        sel = n_valid >= 2
        frac_excluded = 1.0 - sel.float().mean().item()
        if sel.any():
            target = RL["target_entropy_frac"] * torch.log(n_valid[sel])
            loss_a = (self.log_alpha.exp() * (ent[sel] - target)).mean()
            self.opt_a.zero_grad(); loss_a.backward(); self.opt_a.step()

        # Polyak target update
        with torch.no_grad():
            for net, tgt in ((self.q1, self.q1t), (self.q2, self.q2t)):
                for pp, tp in zip(net.parameters(), tgt.parameters()):
                    tp.mul_(1 - RL["tau"]).add_(RL["tau"] * pp)
        self.grad_step += 1
        td = (0.5 * (d1.abs() + d2.abs())).detach().numpy()
        return {"loss_q": float(loss_q.detach()), "loss_pi": float(loss_pi.detach()), "alpha": self.alpha,
                "entropy": float(ent[sel].mean()) if sel.any() else float("nan"),
                "q_mean": float(q1.detach().mean()), "frac_excluded": frac_excluded, "td": td}

    def state_dict(self) -> dict:
        return {"actor": self.actor.state_dict(), "q1": self.q1.state_dict(),
                "q2": self.q2.state_dict(), "q1t": self.q1t.state_dict(),
                "q2t": self.q2t.state_dict(), "log_alpha": self.log_alpha.detach(),
                "grad_step": self.grad_step}

    def load_state_dict(self, sd: dict):
        for k in ("actor", "q1", "q2", "q1t", "q2t"):
            getattr(self, k).load_state_dict(sd[k])
        with torch.no_grad():
            self.log_alpha.copy_(sd["log_alpha"])
        self.grad_step = sd["grad_step"]
