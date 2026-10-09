"""Fault-injector pure logic (CLAUDE.md §5.10): plan sampling, StressChaos manifest, cure predicates.

Cure predicates run on real recorded ticks (tests/fixtures/ticks_steady.jsonl) and the real golden
templates, altered only in the field each predicate reads.
"""

from __future__ import annotations

import copy
import dataclasses
import random
from collections import Counter

from env.golden import load_golden
from env.injector import (
    FAULT_ENV,
    f1_cured,
    f2_cured,
    f3_cured,
    sample_plan,
    stresschaos_manifest,
    surge_users,
)


def test_sample_plan_is_seeded_and_within_contract(contract):
    a = [sample_plan(random.Random(7), contract) for _ in range(1)]
    b = [sample_plan(random.Random(7), contract) for _ in range(1)]
    assert a == b                                                  # same seed, same plan
    rng = random.Random(2026)
    plans = [sample_plan(rng, contract) for _ in range(4000)]
    e = contract.episode
    counts = Counter(p.fault for p in plans)
    for f, prob in e.fault_probs.items():
        assert abs(counts[f] / len(plans) - prob) < 0.03            # matches fault_probs
    for p in plans:
        assert e.lead_in_ticks[0] <= p.lead_in <= e.lead_in_ticks[1]
        if p.fault == "F1":
            assert p.target == "productcatalogservice" and p.severity in e.f1_latency
        elif p.fault == "F2":
            assert p.target in e.f2_targets and p.severity in e.f2_workers
        elif p.fault == "F3":
            assert p.target in e.f3_targets and p.severity is None
        elif p.fault == "F4":
            assert p.target == "frontend" and p.severity in e.f4_multiplier
        else:
            assert p.target is None


def test_stresschaos_manifest_matches_contract_spec():
    m = stresschaos_manifest("f2-run-3", "boutique", "currencyservice", 2)
    assert m["kind"] == "StressChaos" and m["spec"]["mode"] == "one"
    assert m["spec"]["selector"] == {"namespaces": ["boutique"], "labelSelectors": {"app": "currencyservice"}}
    assert m["spec"]["containerNames"] == ["server"]
    assert m["spec"]["stressors"] == {"cpu": {"workers": 2, "load": 100}}


def test_surge_users():
    assert surge_users(2.5, 40) == 100 and surge_users(3.0, 40) == 120


def test_f1_cure_needs_clean_template_and_rollout(contract, steady_raw):
    raw = steady_raw[-1]
    tpl = copy.deepcopy(load_golden(contract)["productcatalogservice"].template)
    assert f1_cured(tpl, raw)
    faulty = copy.deepcopy(tpl)
    faulty["spec"]["containers"][0].setdefault("env", []).append({"name": FAULT_ENV, "value": "600ms"})
    assert not f1_cured(faulty, raw)
    items = dict(raw.deployments.items)
    items["productcatalogservice"] = dataclasses.replace(items["productcatalogservice"], observed_generation=0)
    rolling = dataclasses.replace(raw, deployments=dataclasses.replace(raw.deployments, items=items))
    assert not f1_cured(tpl, rolling)                               # clean template but rollout not complete


def test_f2_cure_when_stressed_pod_uid_is_gone(steady_raw):
    raw = steady_raw[-1]
    uid = raw.pods.pod_uids["cartservice"][0]
    assert f2_cured(uid, raw, "cartservice") is False
    assert f2_cured("some-replaced-pod-uid", raw, "cartservice") is True
    failed = dataclasses.replace(raw, pods=dataclasses.replace(raw.pods, ok=False))
    assert f2_cured(uid, failed, "cartservice") is None             # unknown, not cured


def test_f3_cure_when_a_replica_is_available(steady_raw):
    raw = steady_raw[-1]
    assert f3_cured(raw, "currencyservice")
    items = dict(raw.deployments.items)
    items["currencyservice"] = dataclasses.replace(items["currencyservice"], spec_replicas=0, status_replicas=0,
                                                   available_replicas=0)
    zero = dataclasses.replace(raw, deployments=dataclasses.replace(raw.deployments, items=items))
    assert not f3_cured(zero, "currencyservice")


def test_chaos_name_is_rfc1123_valid():
    import re

    from env.injector import chaos_name
    for run, ep in [("fault_smoke-F2-20261009T170118", 1), ("A__B..C", 12), ("x" * 100, 345)]:
        n = chaos_name(run, ep)
        assert re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", n) and len(n) <= 63, n
        assert n.endswith(f"-{ep}")
    assert chaos_name("fault_smoke-F2-20261009T170118", 1) == "f2-fault-smoke-f2-20261009t170118-1"


def test_f1_injected_event_does_not_duplicate_fields(contract):
    """Regression (2026-10-09): F1's detail repeated `severity`, crashing on_tick after the patch."""
    from kubernetes import client

    from env.injector import FaultPlan, Injector
    from env.recorder import EventLog
    events = EventLog(None)
    inj = Injector(contract, client.ApiClient(client.Configuration()), "http://loc.invalid", 33, "t", events)
    inj.apps.patch_namespaced_deployment = lambda *a, **kw: None          # only the I/O call is stubbed
    inj.arm(FaultPlan("F1", "productcatalogservice", "600ms", lead_in=2), 1)
    res = inj.on_tick(2)
    assert res is not None and res.ok and res.fault == "F1"
    injected = [e for e in events.events if e["event"] == "injected"]
    assert len(injected) == 1 and injected[0]["severity"] == "600ms"
