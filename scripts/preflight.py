"""Staged readiness checks. Read-only: never mutates the cluster.

Usage:  source config/cluster.env && python -m scripts.preflight --stage m1

Exit code 0 iff every check in the stage PASSes.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import re
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests
import urllib3
from kubernetes import client
from kubernetes.client.exceptions import ApiException
from kubernetes.utils import parse_quantity

from scripts._kube import (
    GOLDEN_LIVE_PATH,
    REPO_ROOT,
    SNAPSHOT_BASE_PATH,
    api_client,
    golden_deployments,
    k8s_timeout,
    load_contract,
    rollout_complete,
    template_hash,
)

# --- Gate thresholds ---
# ENVIRONMENT OVERRIDE (human-approved 2026-10-01): A and B are linked over a wireless
# hotspot and A runs other workloads, so the three thresholds marked [override] are relaxed
# from the documented gates (PLAN.md M1: RTT p99 < 2 ms, idle used < 5 GiB; CLAUDE.md §8.10:
# skew < 200 ms). Results under these values are NOT comparable to the documented design.
LAN_RTT_P99_MAX_MS = 800.0        # [override] documented gate: 2.0 (PLAN.md Exit Gate M1)
LAN_PING_COUNT = 200              # PLAN.md: ping -c 200
LAN_PING_INTERVAL_S = 0.2         # smallest interval allowed without root
LAN_PING_PER_REPLY_TIMEOUT_S = 1
CLOCK_SKEW_MAX_MS = 1000.0        # [override] documented gate: 200.0 (CLAUDE.md §8.10)
CLOCK_PROBE_BUDGET_S = 4.0        # time spent sampling server Date headers
A_IDLE_MEMORY_MAX_BYTES = 10 * 1024**3  # [override] documented gate: 5 GiB (PLAN.md M1 idle "used")
FRONTEND_TIMEOUT_S = (2.0, 5.0)
FRONTEND_NODE_PORT = 30080
REQUIRED_ENV = ("A_IP", "NS", "KUBE_ADMIN", "KUBE_AGENT", "KUBE_CTRL", "FRONTEND_URL", "OB_VERSION")
REQUIRED_GITIGNORE = ("config/kube/", "data/", ".venv/")
INJECT_KEY = "chaos-mesh.org/inject"
INJECT_VALUE = "enabled"

# (verb, group, resource, subresource, namespace, expected_allowed)
AccessCase = tuple[str, str, str, str, str, bool]


def _agent_matrix(ns: str) -> list[AccessCase]:
    return [
        ("get", "apps", "deployments", "", ns, True),
        ("list", "apps", "deployments", "", ns, True),
        ("watch", "apps", "deployments", "", ns, True),
        ("patch", "apps", "deployments", "", ns, True),
        ("get", "apps", "deployments", "scale", ns, True),
        ("patch", "apps", "deployments", "scale", ns, True),
        ("get", "", "pods", "", ns, True),
        ("list", "", "pods", "", ns, True),
        ("watch", "", "pods", "", ns, True),
        ("delete", "", "pods", "", ns, False),
        ("delete", "apps", "deployments", "", ns, False),
        ("create", "apps", "deployments", "", ns, False),
        ("update", "apps", "deployments", "", ns, False),
        ("create", "", "pods", "exec", ns, False),
        ("create", "chaos-mesh.org", "stresschaos", "", ns, False),
        ("list", "", "pods", "", "kube-system", False),
        ("patch", "apps", "deployments", "", "kube-system", False),
        ("list", "", "secrets", "", ns, False),
    ]


def _controller_matrix(ns: str) -> list[AccessCase]:
    return [
        ("get", "apps", "deployments", "", ns, True),
        ("list", "apps", "deployments", "", ns, True),
        ("patch", "apps", "deployments", "", ns, True),
        ("get", "apps", "deployments", "scale", ns, True),
        ("patch", "apps", "deployments", "scale", ns, True),
        ("get", "", "pods", "", ns, True),
        ("list", "", "pods", "", ns, True),
        ("delete", "", "pods", "", ns, True),
        ("create", "chaos-mesh.org", "stresschaos", "", ns, True),
        ("get", "chaos-mesh.org", "stresschaos", "", ns, True),
        ("list", "chaos-mesh.org", "stresschaos", "", ns, True),
        ("delete", "chaos-mesh.org", "stresschaos", "", ns, True),
        ("delete", "apps", "deployments", "", ns, False),
        ("create", "apps", "deployments", "", ns, False),
        ("create", "", "pods", "exec", ns, False),
        ("create", "chaos-mesh.org", "networkchaos", "", ns, False),
        ("list", "", "secrets", "", ns, False),
        ("delete", "", "pods", "", "kube-system", False),
        ("create", "chaos-mesh.org", "stresschaos", "", "default", False),
    ]


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Ctx:
    contract: dict[str, Any]
    ns: str
    timeout: tuple[float, float]
    env: dict[str, str]


CHECK_ERRORS = (
    ApiException,
    urllib3.exceptions.HTTPError,
    requests.exceptions.RequestException,
    subprocess.SubprocessError,
    OSError,
    KeyError,
    ValueError,
    TypeError,
    AttributeError,
    json.JSONDecodeError,
)


def _admin(ctx: Ctx) -> client.ApiClient:
    return api_client(ctx.env["KUBE_ADMIN"])


# ----------------------------------------------------------------------------- local checks

def check_contract(ctx: Ctx) -> CheckResult:
    c = ctx.contract
    managed = c["cluster"]["managed"]
    problems = []
    if managed != ["frontend", "cartservice", "currencyservice", "productcatalogservice"]:
        problems.append(f"managed order {managed}")
    for key in ("base", "max"):
        if list(c["replicas"][key]) != managed:
            problems.append(f"replicas.{key} keys/order != managed")
    if any(c["replicas"]["max"][d] < c["replicas"]["base"][d] for d in managed):
        problems.append("max < base")
    expected_denominator = sum(c["replicas"]["max"][d] - c["replicas"]["base"][d] for d in managed)
    if c["reward"]["replica_denominator"] != expected_denominator:
        problems.append(f"replica_denominator != Σ(max-base)={expected_denominator}")
    if not math.isclose(sum(c["episode"]["fault_probs"].values()), 1.0):
        problems.append("fault_probs do not sum to 1")
    return CheckResult("contract.yaml valid", not problems, "; ".join(problems) or f"namespace={ctx.ns}")


def check_env(ctx: Ctx) -> CheckResult:
    missing = [k for k in REQUIRED_ENV if not ctx.env.get(k)]
    detail = f"missing {missing} (source config/cluster.env)" if missing else f"OB_VERSION={ctx.env['OB_VERSION']}"
    return CheckResult("cluster.env sourced", not missing, detail)


def check_gitignore(ctx: Ctx) -> CheckResult:
    lines = {ln.strip() for ln in (REPO_ROOT / ".gitignore").read_text().splitlines()}
    missing = [p for p in REQUIRED_GITIGNORE if p not in lines]
    return CheckResult(".gitignore covers secrets/data", not missing, f"missing {missing}" if missing else "ok")


def check_kubeconfig_files(ctx: Ctx) -> CheckResult:
    problems = []
    for key in ("KUBE_ADMIN", "KUBE_AGENT", "KUBE_CTRL"):
        path = Path(ctx.env[key])
        if not path.is_file():
            problems.append(f"{path.name} missing")
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            problems.append(f"{path.name} mode {oct(mode)} (want 600)")
    tracked = subprocess.run(["git", "ls-files", "config/kube"], cwd=REPO_ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
    if tracked:
        problems.append(f"credentials tracked by git: {tracked}")
    return CheckResult("kubeconfigs present, 0600, untracked", not problems, "; ".join(problems) or "ok")


# ----------------------------------------------------------------------------- cluster checks

def check_node(ctx: Ctx) -> CheckResult:
    nodes = client.CoreV1Api(_admin(ctx)).list_node(_request_timeout=ctx.timeout).items
    ready = [n.metadata.name for n in nodes
             if any(c.type == "Ready" and c.status == "True" for c in n.status.conditions or [])]
    ok = len(nodes) == 1 and len(ready) == 1
    ver = nodes[0].status.node_info.kubelet_version if nodes else "?"
    return CheckResult("single node Ready", ok, f"nodes={len(nodes)} ready={ready} kubelet={ver}")


def check_traefik_disabled(ctx: Ctx) -> CheckResult:
    deps = client.AppsV1Api(_admin(ctx)).list_namespaced_deployment(
        "kube-system", _request_timeout=ctx.timeout).items
    found = [d.metadata.name for d in deps if "traefik" in d.metadata.name]
    return CheckResult("traefik disabled", not found, f"found {found}" if found else "absent")


def check_metrics_server(ctx: Ctx) -> CheckResult:
    svc = client.ApiregistrationV1Api(_admin(ctx)).read_api_service(
        "v1beta1.metrics.k8s.io", _request_timeout=ctx.timeout)
    available = any(c.type == "Available" and c.status == "True" for c in svc.status.conditions or [])
    return CheckResult("metrics-server available", available, "metrics.k8s.io Available" if available else "not Available")


def check_namespace(ctx: Ctx) -> CheckResult:
    ns = client.CoreV1Api(_admin(ctx)).read_namespace(ctx.ns, _request_timeout=ctx.timeout)
    label = (ns.metadata.labels or {}).get(INJECT_KEY)
    annot = (ns.metadata.annotations or {}).get(INJECT_KEY)
    ok = label == INJECT_VALUE and annot == INJECT_VALUE
    return CheckResult(f"namespace {ctx.ns} chaos-inject label+annotation", ok, f"label={label} annotation={annot}")


def check_deployments(ctx: Ctx) -> CheckResult:
    expected = golden_deployments()
    deps = client.AppsV1Api(_admin(ctx)).list_namespaced_deployment(ctx.ns, _request_timeout=ctx.timeout).items
    live = {d.metadata.name: d for d in deps}
    problems = []
    if "loadgenerator" in live:
        problems.append("loadgenerator present")
    if set(live) != set(expected):
        problems.append(f"missing={sorted(set(expected) - set(live))} extra={sorted(set(live) - set(expected))}")
    for name, dep in sorted(live.items()):
        if name in expected and dep.spec.replicas != expected[name]:
            problems.append(f"{name} replicas={dep.spec.replicas}")
        if not rollout_complete(dep):
            problems.append(f"{name} not rollout-complete "
                            f"(avail={dep.status.available_replicas}/{dep.spec.replicas})")
    for name in ctx.contract["cluster"]["managed"]:
        if name in live and [c.name for c in live[name].spec.template.spec.containers] != ["server"]:
            problems.append(f"{name} containers != ['server']")
    return CheckResult(f"{len(expected)} golden deployments available", not problems,
                       "; ".join(problems) or f"{len(live)} deployments, all rollout-complete at golden replicas")


def check_frontend_service(ctx: Ctx) -> CheckResult:
    svc = client.CoreV1Api(_admin(ctx)).read_namespaced_service(
        "frontend-external", ctx.ns, _request_timeout=ctx.timeout)
    ports = [p.node_port for p in svc.spec.ports]
    ok = svc.spec.type == "NodePort" and ports == [FRONTEND_NODE_PORT]
    return CheckResult("frontend-external NodePort 30080", ok, f"type={svc.spec.type} nodePorts={ports}")


def check_frontend_http(ctx: Ctx) -> CheckResult:
    url = ctx.env["FRONTEND_URL"].rstrip("/") + "/"
    t0 = time.monotonic()
    resp = requests.get(url, timeout=FRONTEND_TIMEOUT_S, allow_redirects=False)
    ms = (time.monotonic() - t0) * 1000
    return CheckResult("frontend HTTP 200", resp.status_code == 200, f"GET {url} -> {resp.status_code} in {ms:.0f} ms")


def _access_matrix(ctx: Ctx, kubeconfig_key: str, label: str, cases: list[AccessCase]) -> CheckResult:
    authz = client.AuthorizationV1Api(api_client(ctx.env[kubeconfig_key]))
    mismatches = []
    for verb, group, resource, sub, ns, want in cases:
        review = client.V1SelfSubjectAccessReview(spec=client.V1SelfSubjectAccessReviewSpec(
            resource_attributes=client.V1ResourceAttributes(
                verb=verb, group=group, resource=resource, subresource=sub or None, namespace=ns)))
        got = authz.create_self_subject_access_review(review, _request_timeout=ctx.timeout).status.allowed
        if got != want:
            res = f"{resource}/{sub}" if sub else resource
            mismatches.append(f"{verb} {res} -n {ns}: allowed={got}, want {want}")
    return CheckResult(f"{label} RBAC least-privilege", not mismatches,
                       "; ".join(mismatches) or f"{len(cases)} access cases match")


def check_agent_rbac(ctx: Ctx) -> CheckResult:
    return _access_matrix(ctx, "KUBE_AGENT", "agent", _agent_matrix(ctx.ns))


def check_controller_rbac(ctx: Ctx) -> CheckResult:
    return _access_matrix(ctx, "KUBE_CTRL", "controller", _controller_matrix(ctx.ns))


def check_golden_live(ctx: Ctx) -> CheckResult:
    snap = json.loads(GOLDEN_LIVE_PATH.read_text())
    problems = []
    if SNAPSHOT_BASE_PATH.read_bytes() != GOLDEN_LIVE_PATH.read_bytes():
        problems.append(f"{SNAPSHOT_BASE_PATH.name} differs from {GOLDEN_LIVE_PATH.name}")
    api = api_client(ctx.env["KUBE_AGENT"])  # read via the least-privilege credential
    deps = client.AppsV1Api(api).list_namespaced_deployment(ctx.ns, _request_timeout=ctx.timeout).items
    live = {d.metadata.name: d for d in deps}
    for name, entry in sorted(snap["deployments"].items()):
        dep = live.get(name)
        if dep is None:
            problems.append(f"{name} missing live")
            continue
        if template_hash(api.sanitize_for_serialization(dep.spec.template)) != entry["template_hash"]:
            problems.append(f"{name} template drift")
        if dep.spec.replicas != entry["replicas"]:
            problems.append(f"{name} replicas {dep.spec.replicas} != {entry['replicas']}")
    for name in ctx.contract["cluster"]["managed"]:
        if "limits" not in snap["deployments"].get(name, {}):
            problems.append(f"{name} has no limits in snapshot")
    return CheckResult("golden_live.json matches live", not problems,
                       "; ".join(problems) or f"{len(snap['deployments'])} templates match (git {snap['git_sha'][:8]})")


def check_golden_committed(ctx: Ctx) -> CheckResult:
    rel = str(GOLDEN_LIVE_PATH.relative_to(REPO_ROOT))
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=REPO_ROOT,
                             capture_output=True, text=True, check=False).returncode == 0
    dirty = subprocess.run(["git", "status", "--porcelain", "--", rel], cwd=REPO_ROOT,
                           capture_output=True, text=True, check=True).stdout.strip()
    ok = tracked and not dirty
    return CheckResult("golden_live.json committed", ok,
                       "tracked and clean" if ok else ("untracked" if not tracked else "uncommitted changes"))


def check_lan_rtt(ctx: Ctx) -> CheckResult:
    a_ip = ctx.env["A_IP"]
    proc = subprocess.run(
        ["ping", "-n", "-c", str(LAN_PING_COUNT), "-i", str(LAN_PING_INTERVAL_S),
         "-W", str(LAN_PING_PER_REPLY_TIMEOUT_S), a_ip],
        capture_output=True, text=True, check=False,
        timeout=LAN_PING_COUNT * LAN_PING_INTERVAL_S + 10 * LAN_PING_PER_REPLY_TIMEOUT_S,
    )
    rtts = sorted(float(m) for m in re.findall(r"time=([\d.]+) ms", proc.stdout))
    if not rtts:
        return CheckResult(f"LAN RTT p99 < {LAN_RTT_P99_MAX_MS:g} ms", False, f"no replies from {a_ip}")
    p99 = rtts[max(0, math.ceil(0.99 * len(rtts)) - 1)]
    lost = LAN_PING_COUNT - len(rtts)
    ok = p99 < LAN_RTT_P99_MAX_MS and lost == 0
    return CheckResult(f"LAN RTT p99 < {LAN_RTT_P99_MAX_MS:g} ms", ok,
                       f"n={len(rtts)} lost={lost} median={rtts[len(rtts) // 2]:.2f} p99={p99:.2f} max={rtts[-1]:.2f} ms")


def check_clock_skew(ctx: Ctx) -> CheckResult:
    """Estimate A-B skew from the API server's 1 s-resolution HTTP Date header.

    Sample back-to-back requests; when the Date second ticks over between samples i-1 and i,
    the server's second boundary lies in B-monotonic [send(i-1), recv(i)]. Skew = boundary
    (server) - bracket midpoint converted to B wall time; uncertainty = half the bracket width.

    B's wall-minus-monotonic offset is tracked on every sample: if B's wall clock is stepped
    during the probe (NTP/hypervisor sync), the step size is reported and counted against the
    budget, since B is the clock authority.
    """
    api = _admin(ctx)
    samples: list[tuple[float, float, float, float]] = []  # (mono_send, mono_recv, wall_offset, server_second)
    offsets: list[float] = []
    deadline = time.monotonic() + CLOCK_PROBE_BUDGET_S
    while time.monotonic() < deadline:
        m_send = time.monotonic()
        offsets.append(time.time() - m_send)
        resp = api.call_api("/version", "GET", auth_settings=["BearerToken"], _preload_content=False,
                            _return_http_data_only=True, _request_timeout=ctx.timeout)
        m_recv = time.monotonic()
        offsets.append(time.time() - m_recv)
        server_s = parsedate_to_datetime(resp.headers["Date"]).timestamp()
        samples.append((m_send, m_recv, (offsets[-2] + offsets[-1]) / 2, server_s))
        resp.drain_conn()
        resp.release_conn()
    step_ms = (max(offsets) - min(offsets)) * 1000 if offsets else 0.0
    best: tuple[float, float] | None = None  # (skew_s, uncertainty_s)
    for (s0, _r0, off0, d0), (_s1, r1, off1, d1) in itertools.pairwise(samples):
        if d1 == d0 + 1:
            est = (d1 - ((s0 + r1) / 2 + (off0 + off1) / 2), (r1 - s0) / 2)
            if best is None or est[1] < best[1]:
                best = est
    if best is None:
        return CheckResult(f"A-B clock skew < {CLOCK_SKEW_MAX_MS:g} ms", False, f"no Date transition in {len(samples)} samples")
    skew_ms, unc_ms = best[0] * 1000, best[1] * 1000
    worst_ms = abs(skew_ms) + unc_ms + step_ms
    ok = worst_ms < CLOCK_SKEW_MAX_MS
    detail = f"skew={skew_ms:+.0f} ± {unc_ms:.0f} ms, B wall-clock step during probe={step_ms:.0f} ms"
    if not ok:
        detail += " (exceeds)" if abs(skew_ms) - unc_ms >= CLOCK_SKEW_MAX_MS else " (cannot prove < limit)"
    return CheckResult(f"A-B clock skew < {CLOCK_SKEW_MAX_MS:g} ms", ok, detail)


def check_a_idle_memory(ctx: Ctx) -> CheckResult:
    usage = client.CustomObjectsApi(_admin(ctx)).list_cluster_custom_object(
        "metrics.k8s.io", "v1beta1", "nodes", _request_timeout=ctx.timeout)
    items = usage["items"]
    used = sum(int(parse_quantity(i["usage"]["memory"])) for i in items)
    gib = used / 1024**3
    return CheckResult(f"A idle memory < {A_IDLE_MEMORY_MAX_BYTES / 1024**3:g} GiB", used < A_IDLE_MEMORY_MAX_BYTES,
                       f"node working set {gib:.2f} GiB (metrics-server)")


STAGES: dict[str, list[Callable[[Ctx], CheckResult]]] = {
    "m1": [
        check_contract, check_env, check_gitignore, check_kubeconfig_files,
        check_node, check_traefik_disabled, check_metrics_server, check_namespace,
        check_deployments, check_frontend_service, check_frontend_http,
        check_agent_rbac, check_controller_rbac,
        check_golden_live, check_golden_committed,
        check_a_idle_memory, check_clock_skew, check_lan_rtt,
    ],
}


def run_stage(stage: str, ctx: Ctx) -> list[CheckResult]:
    results = []
    for check in STAGES[stage]:
        try:
            res = check(ctx)
        except CHECK_ERRORS as exc:
            res = CheckResult(check.__name__.removeprefix("check_"), False, f"{type(exc).__name__}: {exc}"[:300])
        results.append(res)
        print(f"[{'PASS' if res.ok else 'FAIL'}] {res.name}: {res.detail}", flush=True)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=sorted(STAGES), required=True)
    args = parser.parse_args(argv)

    contract = load_contract()
    ctx = Ctx(contract=contract, ns=contract["cluster"]["namespace"], timeout=k8s_timeout(contract),
              env=dict(os.environ))
    results = run_stage(args.stage, ctx)
    n_fail = sum(not r.ok for r in results)
    print(f"\npreflight {args.stage}: {len(results) - n_fail}/{len(results)} PASS"
          + ("" if n_fail == 0 else f", {n_fail} FAIL"))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
