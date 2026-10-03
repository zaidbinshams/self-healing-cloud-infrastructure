"""Pure-math tests (CLAUDE.md §4.1 rule 3: tiny hand-written tensors only)."""
import math

import numpy as np
import torch

from prototype.core import compute_mask, reward, sla_violation
from prototype.masked_sac import MaskedDiscreteSAC, masked_policy
from prototype.per_buffer import ReplayBuffer, nstep_transitions


def test_masked_actions_get_zero_probability_and_no_nan():
    logits = torch.tensor([[1.0, 5.0, -2.0, 0.0]])
    mask = torch.tensor([[True, False, True, False]])
    p, logp, _ = masked_policy(logits, mask)
    assert torch.all(p[~mask] == 0)
    assert torch.isfinite(logp).all()
    assert math.isclose(float(p.sum()), 1.0, rel_tol=1e-6)
    ent = -(p * logp).sum()
    assert torch.isfinite(ent)


def test_single_valid_action_excluded_from_alpha_loss():
    agent = MaskedDiscreteSAC(0)
    B = 4
    m = np.zeros((B, 12), bool); m[:, 0] = True       # only NOOP valid everywhere
    b = {"s": np.zeros((B, 36), np.float32), "m": m, "a": np.zeros(B, np.int64),
         "R": np.zeros(B, np.float32), "s2": np.zeros((B, 36), np.float32), "m2": m,
         "term": np.zeros(B, np.float32), "gn": np.full(B, 0.93 ** 3, np.float32)}
    a0 = agent.alpha
    out = agent.update(b, np.ones(B, np.float32))
    assert out["frac_excluded"] == 1.0
    assert agent.alpha == a0                          # alpha untouched


def test_masked_soft_value_ignores_invalid_actions():
    q = torch.tensor([[1.0, 100.0, 2.0]])
    mask = torch.tensor([[True, False, True]])
    p, logp, _ = masked_policy(torch.zeros(1, 3), mask)
    qz = torch.where(mask, q, torch.zeros_like(q))
    v = (p * (qz - 0.1 * logp)).sum()
    expected = 0.5 * 1 + 0.5 * 2 - 0.1 * math.log(0.5)
    assert math.isclose(float(v), expected, rel_tol=1e-5)


def test_per_priority_cap_eps_and_beta():
    buf = ReplayBuffer(10, True, np.random.default_rng(0))
    z = np.zeros(36, np.float32); mk = np.ones(12, bool)
    for _ in range(4):
        buf.add(z, mk, 0, 0.0, z, mk, 0.0, 0.8)
    buf.update_priorities(np.array([0, 1]), np.array([5.0, 0.0]))
    assert math.isclose(buf.prio[0], 1.0 + 1e-3) and math.isclose(buf.prio[1], 1e-3)
    assert buf.beta(0) == 0.4 and buf.beta(15000) == 1.0 and buf.beta(30000) == 1.0
    _, w, _ = buf.sample(8, 0)
    assert math.isclose(float(w.max()), 1.0)


def test_nstep_cuts_bootstrap_only_on_termination():
    z = np.zeros(36, np.float32); mk = np.ones(12, bool)
    ep = [{"s": z, "m": mk, "a": 0, "r": -1.0, "s2": z, "m2": mk, "terminated": False,
           "stale2": False} for _ in range(4)]
    ep[-1]["terminated"] = True
    tr = nstep_transitions(ep, 0.9, 3)
    assert math.isclose(tr[0][3], -(1 + 0.9 + 0.81)) and tr[0][6] == 0.0
    assert tr[2][6] == 1.0 and math.isclose(tr[2][3], -1.9)


def test_reward_bounds_and_mask_rules():
    spec = {"frontend": 3, "cartservice": 2, "currencyservice": 2, "productcatalogservice": 1}
    r = reward(1e6, 1.0, spec, 2)
    assert math.isclose(r, -1.09, rel_tol=1e-6)
    assert sla_violation(10.0, 0.0) == 0.0
    m = compute_mask({"frontend": 1, "cartservice": 0, "currencyservice": 1,
                      "productcatalogservice": 1}, False)
    assert m[2] == 0 and m[6] == 0 and m[7] == 1 and m[9] == m[10] == m[11] == 1
    assert compute_mask(spec, True).sum() == 1
