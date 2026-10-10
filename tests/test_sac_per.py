"""Pure tests for the masked discrete SAC and PER (CLAUDE.md §5.12), on hand-written tensors."""

from __future__ import annotations

import math

import numpy as np
import pytest
import torch

from agents.masked_discrete_sac import (
    MaskedDiscreteSAC,
    NonFiniteLoss,
    actor_loss,
    alpha_loss,
    entropy,
    masked_policy,
    soft_value,
    td_target,
)
from agents.per_buffer import (
    NStepBuilder,
    ReplayBuffer,
    Step,
    SumTree,
    Transition,
    beta_at,
    priority_from_td,
)
from env.contract import N_ACTIONS, OBS_DIM, load_contract

C = load_contract()


def _mask(*valid: int) -> torch.Tensor:
    m = torch.zeros(N_ACTIONS, dtype=torch.bool)
    m[list(valid)] = True
    return m


# ----------------------------------------------------------------------------- rule 1: masking

def test_masked_policy_zeroes_masked_entries_without_nan():
    logits = torch.tensor([[5.0, 100.0, -3.0] + [50.0] * (N_ACTIONS - 3)])
    mask = _mask(0, 2).unsqueeze(0)
    probs, logp = masked_policy(logits, mask)
    assert torch.all(probs[~mask] == 0) and torch.all(logp[~mask] == 0)
    assert torch.isclose(probs.sum(), torch.tensor(1.0))
    assert torch.isfinite(entropy(probs, logp)).all()


def test_entropy_of_uniform_over_valid_actions_is_log_k():
    probs, logp = masked_policy(torch.zeros(1, N_ACTIONS), _mask(0, 3, 7).unsqueeze(0))
    assert math.isclose(entropy(probs, logp).item(), math.log(3), rel_tol=1e-6)


def test_single_valid_action_has_zero_entropy_and_finite_losses():
    mask = _mask(0).unsqueeze(0)
    probs, logp = masked_policy(torch.randn(1, N_ACTIONS), mask)
    assert probs[0, 0] == 1.0 and entropy(probs, logp).item() == 0.0
    assert torch.isfinite(actor_loss(probs, logp, torch.randn(1, N_ACTIONS), mask, 0.1))


# ----------------------------------------------------------------------------- rule 2: target

def test_soft_value_matches_hand_computation_and_ignores_masked_q():
    mask = _mask(0, 1).unsqueeze(0)
    logits = torch.zeros(1, N_ACTIONS)
    probs, logp = masked_policy(logits, mask)                       # π = (0.5, 0.5) on {0, 1}
    q = torch.full((1, N_ACTIONS), 1e6)                             # masked Q values must not leak in
    q[0, 0], q[0, 1] = 1.0, 3.0
    alpha = 0.2
    expected = 0.5 * (1.0 + alpha * math.log(2)) + 0.5 * (3.0 + alpha * math.log(2))
    assert math.isclose(soft_value(probs, logp, q, mask, alpha).item(), expected, rel_tol=1e-6)


def test_td_target_bootstraps_unless_terminated():
    ret, disc, v = torch.tensor([1.0, 1.0]), torch.tensor([0.9, 0.9]), torch.tensor([10.0, 10.0])
    y = td_target(ret, torch.tensor([0.0, 1.0]), disc, v)
    assert y.tolist() == pytest.approx([10.0, 1.0])


# ----------------------------------------------------------------------------- rule 5: alpha

def test_alpha_loss_excludes_single_action_states_and_has_right_sign():
    log_alpha = torch.tensor(math.log(0.1), requires_grad=True)
    mask = torch.stack([_mask(0), _mask(0, 1, 2, 3)])
    ent = torch.tensor([0.0, 0.1])                                  # below target 0.4·log 4 → α should rise
    loss, excluded = alpha_loss(log_alpha, ent, mask, C.rl.target_entropy_frac)
    assert excluded == 0.5
    loss.backward()
    assert log_alpha.grad is not None and log_alpha.grad.item() < 0   # gradient descent increases log α


def test_alpha_loss_all_single_action_states_gives_zero():
    log_alpha = torch.tensor(0.0, requires_grad=True)
    loss, excluded = alpha_loss(log_alpha, torch.zeros(2), torch.stack([_mask(0), _mask(0)]), 0.4)
    assert loss.item() == 0.0 and excluded == 1.0


# ----------------------------------------------------------------------------- agent

def _transition(rng: np.random.Generator, single_valid: bool = False) -> Transition:
    m = np.zeros(N_ACTIONS, np.int8)
    m[0] = 1
    if not single_valid:
        m[rng.choice(np.arange(1, N_ACTIONS), size=3, replace=False)] = 1
    valid = np.flatnonzero(m)
    return Transition(rng.uniform(-1, 1, OBS_DIM).astype(np.float32), m, int(rng.choice(valid)),
                      float(-rng.uniform(0, 1)), rng.uniform(-1, 1, OBS_DIM).astype(np.float32), m,
                      bool(rng.random() < 0.1), C.rl.gamma ** C.rl.n_step)


def test_update_is_finite_and_returns_bounded_priorities():
    rng = np.random.default_rng(0)
    buf = ReplayBuffer(256, C.per, seed=0)
    for i in range(200):
        buf.add(_transition(rng, single_valid=(i % 5 == 0)))
    agent = MaskedDiscreteSAC(C.rl, C.per, seed=0)
    for _ in range(5):
        b = buf.sample(C.rl.batch_size, beta_at(C.per, agent.grad_step))
        stats, prio = agent.update(b)
        buf.update_priorities(b.idx, prio)
    assert agent.grad_step == 5
    assert all(math.isfinite(v) for v in vars(stats).values())
    assert prio.shape == (C.rl.batch_size,)
    assert np.all(prio >= C.per.eps) and np.all(prio <= C.per.priority_cap + C.per.eps)


def test_act_never_picks_a_masked_action():
    agent = MaskedDiscreteSAC(C.rl, C.per, seed=0)
    with torch.no_grad():
        agent.actor[-1].bias.fill_(0.0)
        agent.actor[-1].bias[5] = 1e4                                # strongly prefers a masked action
    m = np.zeros(N_ACTIONS, np.int8)
    m[[0, 2]] = 1
    obs = np.zeros(OBS_DIM, np.float32)
    assert agent.act(obs, m, greedy=True) in (0, 2)
    assert {agent.act(obs, m, greedy=False) for _ in range(50)} <= {0, 2}


def test_nonfinite_loss_raises_instead_of_stepping():
    rng = np.random.default_rng(1)
    buf = ReplayBuffer(64, C.per, seed=0)
    for _ in range(32):
        buf.add(_transition(rng))
    b = buf.sample(16, 0.4)
    b.ret[0] = np.nan
    agent = MaskedDiscreteSAC(C.rl, C.per, seed=0)
    with pytest.raises(NonFiniteLoss):
        agent.update(b)
    assert agent.grad_step == 0


def test_checkpoint_roundtrip_restores_parameters_and_step():
    rng = np.random.default_rng(2)
    buf = ReplayBuffer(256, C.per, seed=0)
    for _ in range(150):
        buf.add(_transition(rng))
    a = MaskedDiscreteSAC(C.rl, C.per, seed=0)
    a.update(buf.sample(C.rl.batch_size, 0.4))
    b = MaskedDiscreteSAC(C.rl, C.per, seed=99)
    b.load_state_dict(a.state_dict())
    assert b.grad_step == 1 and torch.equal(a.log_alpha, b.log_alpha)
    for p, q in zip(a.actor.parameters(), b.actor.parameters(), strict=True):
        assert torch.equal(p, q)


# ----------------------------------------------------------------------------- PER

def test_sumtree_total_and_find():
    t = SumTree(5)
    t.set(np.arange(5), np.array([1.0, 2.0, 0.0, 3.0, 4.0]))
    assert t.total == 10.0
    assert [t.find(m) for m in (0.5, 1.5, 3.5, 6.5, 9.99)] == [0, 1, 3, 4, 4]


def test_priority_from_td_caps_and_adds_eps():
    p = priority_from_td(np.array([0.2, 5.0]), np.array([0.4, 5.0]), C.per)
    assert p.tolist() == pytest.approx([0.3 + C.per.eps, C.per.priority_cap + C.per.eps])


def test_beta_anneals_over_gradient_steps():
    assert beta_at(C.per, 0) == C.per.beta_start
    assert beta_at(C.per, C.per.beta_anneal_grad_steps * 2) == C.per.beta_end


def test_new_transitions_get_max_priority_and_sampling_is_proportional():
    rng = np.random.default_rng(3)
    buf = ReplayBuffer(8, C.per, seed=3)
    for _ in range(4):
        buf.add(_transition(rng))
    buf.update_priorities(np.arange(4), np.array([0.01, 0.01, 0.01, 0.81]))
    buf.add(_transition(rng))
    assert buf.priority[4] == pytest.approx(0.81)                    # current max
    counts = np.bincount(np.concatenate([buf.sample(64, 1.0).idx for _ in range(200)]), minlength=5)
    expect = np.array([0.01, 0.01, 0.01, 0.81, 0.81]) ** C.per.alpha
    assert np.allclose(counts / counts.sum(), expect / expect.sum(), atol=0.02)


def test_is_weights_are_normalised_and_uniform_mode_has_unit_weights():
    rng = np.random.default_rng(4)
    buf = ReplayBuffer(16, C.per, seed=4)
    for _ in range(10):
        buf.add(_transition(rng))
    buf.update_priorities(np.arange(10), np.linspace(0.05, 1.0, 10))
    w = buf.sample(32, 0.7).weight
    assert np.all(w <= 1.0 + 1e-6) and np.all(w > 0) and w.max() == pytest.approx(1.0)   # batch-max normalised
    uni = ReplayBuffer(16, C.per, prioritized=False, seed=4)
    for _ in range(10):
        uni.add(_transition(rng))
    assert np.all(uni.sample(32, 0.7).weight == 1.0)


def test_ring_buffer_overwrites_oldest():
    rng = np.random.default_rng(5)
    buf = ReplayBuffer(4, C.per, seed=0)
    for _ in range(6):
        buf.add(_transition(rng))
    assert len(buf) == 4 and buf.next_idx == 2


# ----------------------------------------------------------------------------- n-step

def _step(r: float, *, term: bool = False, trunc: bool = False, stale: bool = False, tag: float = 0.0) -> Step:
    o = np.full(OBS_DIM, tag, np.float32)
    m = np.ones(N_ACTIONS, np.int8)
    return Step(o, m, 0, r, np.full(OBS_DIM, tag + 1, np.float32), m, term, trunc, stale)


def test_nstep_returns_and_discount():
    g = 0.9
    nb = NStepBuilder(g, 3)
    out = []
    for i, r in enumerate([1.0, 2.0, 3.0, 4.0]):
        out += nb.push(_step(r, tag=i))
    assert len(out) == 2
    assert out[0].ret == pytest.approx(1 + g * 2 + g * g * 3) and out[0].discount == pytest.approx(g ** 3)
    assert out[0].next_obs[0] == 3.0 and not out[0].done


def test_nstep_flushes_short_chains_at_termination():
    g = 0.9
    nb = NStepBuilder(g, 3)
    out = nb.push(_step(1.0, tag=0)) + nb.push(_step(2.0, tag=1, term=True))
    assert [t.done for t in out] == [True, True]
    assert out[0].ret == pytest.approx(1 + g * 2) and out[0].discount == pytest.approx(g ** 2)
    assert out[1].ret == pytest.approx(2.0) and not nb.pending


def test_nstep_truncation_bootstraps():
    nb = NStepBuilder(0.9, 3)
    out = nb.push(_step(1.0, tag=0)) + nb.push(_step(1.0, tag=1, trunc=True))
    assert len(out) == 2 and not any(t.done for t in out)


def test_stale_next_state_is_never_stored_and_breaks_the_chain():
    g = 0.9
    nb = NStepBuilder(g, 3)
    out = nb.push(_step(1.0, tag=0)) + nb.push(_step(2.0, tag=1))
    out += nb.push(_step(5.0, tag=2, stale=True))                   # s′ stale: closes pending, dropped
    assert len(out) == 2 and nb.dropped_stale == 1
    assert out[0].ret == pytest.approx(1 + g * 2) and out[0].next_obs[0] == 2.0
    assert all(t.next_obs[0] != 3.0 for t in out)                   # nothing bootstraps from the stale s′


# ----------------------------------------------------------------------------- warm-start loader / train helpers

def _record(tick: int, *, r: float = -0.1, term: bool = False, stale: bool = False, source: str = "runbook") -> dict:
    m = [1] + [0] * (N_ACTIONS - 1)
    return {"tick": tick, "obs": [float(tick)] * OBS_DIM, "mask": m, "a_exec": 0, "reward": r,
            "next_obs": [float(tick + 1)] * OBS_DIM, "next_mask": m, "terminated": term, "truncated": False,
            "stale": stale, "source": source}


def test_load_transitions_skips_probes_drops_stale_and_closes_interrupted_files(tmp_path):
    import json

    from agents.warmstart import load_transitions
    d = tmp_path / "run"
    d.mkdir()
    ep1 = [_record(0), _record(1, stale=True), _record(2), _record(3, term=True)]
    ep2 = [_record(0, source="probe"), _record(1, source="probe")]
    ep3 = [_record(10), _record(11)]                                 # interrupted: no terminal flag
    for i, ep in enumerate((ep1, ep2, ep3), start=1):
        (d / f"episode_{i}.jsonl").write_text("\n".join(json.dumps(r) for r in ep) + "\n")
    ts, st = load_transitions([d], 0.9, 3)
    assert st.episodes == 2 and st.skipped_probe_steps == 2 and st.dropped_stale == 1
    # ep1: step0 closed at the stale break (1 transition), steps 2-3 flushed at termination (2); ep3: 2
    assert st.transitions == len(ts) == 5
    assert all(t.next_obs[0] != 2.0 for t in ts)                     # the stale s′ (tick 1 → obs 2) never used


def test_q_minus_return_on_a_short_episode():
    from agents.train import q_minus_return
    agent = MaskedDiscreteSAC(C.rl, C.per, seed=0)
    for net in (agent.critic.q1, agent.critic.q2):
        with torch.no_grad():
            for p in net.parameters():
                p.zero_()
            net[-1].bias.fill_(-1.0)                                # Q ≡ −1 everywhere
    steps = [_step(-0.5, tag=0), _step(-0.5, tag=1, term=True)]
    g = 0.9
    expected = np.mean([-1 - (-0.5 + g * -0.5), -1 - (-0.5)])
    assert q_minus_return(agent, steps, g) == pytest.approx(expected)
