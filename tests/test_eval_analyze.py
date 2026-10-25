"""eval/analyze.py on tiny hand-written rows (pure; no real or fabricated results)."""

from __future__ import annotations

from eval.analyze import analyze, final_valid

MTTR = {"sac_per": 60.0, "runbook": 70.0, "sac_per_cold": 100.0, "k8s_default": 360.0, "k8s_hpa": 360.0}


def row(policy, index, fault, *, valid=True, actions=1):
    m = MTTR[policy]
    return {"policy": policy, "index": index, "fault": fault, "valid": valid, "recovered": fault != "NULL" and m < 360,
            "mttr_censored_s": m + index % 3, "event_observed": fault != "NULL" and m < 360, "sla_penalty": m / 60,
            "actions": 0 if fault == "NULL" else actions, "false_remediation": False, "mask_violations": 0,
            "exec_errors": 0}


def rows():
    out, i = [], 0
    for fault in ("F1", "F2", "F3", "F4", "NULL"):
        for _ in range(6):
            out += [row(p, i, fault) for p in MTTR]
            i += 1
    return out


def test_final_valid_keeps_last_valid_attempt_and_counts_exclusions():
    rs = [row("runbook", 0, "F1", valid=False), row("runbook", 0, "F1")]
    keep, excl = final_valid(rs)
    assert len(keep) == 1 and excl == {"runbook": 1}


def test_analysis_structure_and_obvious_directions():
    train = {"sac_per": [{"return": -1.0, "fault": "F1"}] * 100, "sac_per_cold": [{"return": -3.0, "fault": "F1"}] * 100}
    res = analyze(rows(), [], train)
    assert set(res["policies"]) == set(MTTR)
    f1 = {c["b"]: c for c in res["H1"]["F1"]}
    assert f1["k8s_default"]["cliffs_delta"] == 1.0 and f1["k8s_default"]["p_holm"] < 0.05
    assert res["H2"]["pooled"]["mttr"]["non_inferior"]
    assert res["H3"]["train_return_1_100"]["cliffs_delta"] == -1.0      # warm start: higher return
    assert res["H4"]["eval_null_episodes"] == 6 and res["H4"]["eval_null_with_action"] == 0
    assert res["km"]["logrank_vs_sac_per"]["k8s_default"] < 0.05


def test_plots_render_from_hand_written_rows(tmp_path):
    import json

    from eval.plots import fig_km, fig_mttr_by_fault, fig_training
    res = analyze(rows(), [], {})
    fig_km(json.loads(json.dumps(res, default=float)), tmp_path, 360.0)
    fig_mttr_by_fault(final_valid(rows())[0], tmp_path, 360.0)
    fig_training({"sac_per": [{"return": -float(i % 7)} for i in range(30)]}, tmp_path)
    assert {p.name for p in tmp_path.glob("*.pdf")} == {"a1_km.pdf", "a2_mttr_by_fault.pdf", "a3_training.pdf"}


def test_replication_renames_agent_and_checks_anchor_drift():
    from eval.analyze import replication
    main = rows()
    MTTR["sac_per_s1"] = 65.0
    try:
        block = [row(p, r["index"], r["fault"]) for r in main if r["policy"] == "runbook" for p in ("runbook", "sac_per_s1")]
    finally:
        del MTTR["sac_per_s1"]
    res = replication(block, main, "sac_per_s1", [{"fault": "NULL", "actions": 0, "return": 0.0}])
    assert res["H2"]["pooled"]["mttr"]["non_inferior"]                  # 65 vs 70 within one tick
    assert res["anchor_drift"]["pooled"]["median_a"] == res["anchor_drift"]["pooled"]["median_b"]
    assert res["seed1_vs_seed0"]["F1"]["a"] == "sac_per_s1"
    assert res["H4"]["train_null_episodes"] == 1 and res["H4"]["train_frr"] == 0.0
