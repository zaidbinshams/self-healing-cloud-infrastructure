"""Capture the API-defaulted golden state of every `boutique` deployment.

Writes config/golden_live.json (the RESTORE source of truth, CLAUDE.md §5.2) and an
identical copy at k8s/boutique/snapshot-base.json. Refuses to snapshot unless the live
namespace is a verified clean apply of k8s/boutique/golden.yaml:
  * exactly the golden deployment set, each rollout-complete at its golden replica count;
  * no restartedAt annotation and no EXTRA_LATENCY env on any `server` container.

Bootstrap script: uses the admin kubeconfig.  Usage:  python -m scripts.snapshot_golden
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import urllib3
from kubernetes import client
from kubernetes.client.exceptions import ApiException
from kubernetes.utils import parse_quantity

from scripts._kube import (
    CONTRACT_PATH,
    GOLDEN_LIVE_PATH,
    GOLDEN_YAML_PATH,
    REPO_ROOT,
    RESTARTED_AT_ANNOTATION,
    SNAPSHOT_BASE_PATH,
    api_client,
    golden_deployments,
    k8s_timeout,
    load_contract,
    rollout_complete,
    sha256_file,
    template_hash,
)

APP_CONTAINER = "server"
FAULT_ENV = "EXTRA_LATENCY"


class NotCleanError(RuntimeError):
    pass


def _git_sha() -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _server_limits(template: dict[str, Any]) -> dict[str, float | int]:
    containers = template["spec"]["containers"]
    server = next(c for c in containers if c["name"] == APP_CONTAINER)
    limits = server["resources"]["limits"]
    return {
        "cpu_cores": float(parse_quantity(limits["cpu"])),
        "memory_bytes": int(parse_quantity(limits["memory"])),
    }


def verify_clean(deps: list[client.V1Deployment], expected: dict[str, int], api: client.ApiClient) -> None:
    live = {d.metadata.name: d for d in deps}
    problems: list[str] = []
    if set(live) != set(expected):
        problems.append(f"deployment set mismatch: missing={sorted(set(expected) - set(live))} "
                        f"extra={sorted(set(live) - set(expected))}")
    for name, want in expected.items():
        dep = live.get(name)
        if dep is None:
            continue
        if dep.spec.replicas != want:
            problems.append(f"{name}: spec.replicas={dep.spec.replicas}, golden={want}")
        if not rollout_complete(dep):
            problems.append(f"{name}: rollout not complete")
        tpl = api.sanitize_for_serialization(dep.spec.template)
        if RESTARTED_AT_ANNOTATION in ((tpl.get("metadata") or {}).get("annotations") or {}):
            problems.append(f"{name}: has {RESTARTED_AT_ANNOTATION} (not a clean apply)")
        for c in tpl["spec"]["containers"]:
            if any(e.get("name") == FAULT_ENV for e in c.get("env") or []):
                problems.append(f"{name}/{c['name']}: has {FAULT_ENV} env")
    if problems:
        raise NotCleanError("; ".join(problems))


def build_snapshot(contract: dict[str, Any], kubeconfig: str) -> dict[str, Any]:
    ns = contract["cluster"]["namespace"]
    managed = contract["cluster"]["managed"]
    expected = golden_deployments()
    api = api_client(kubeconfig)
    deps = client.AppsV1Api(api).list_namespaced_deployment(ns, _request_timeout=k8s_timeout(contract)).items
    verify_clean(deps, expected, api)

    deployments: dict[str, Any] = {}
    for dep in sorted(deps, key=lambda d: d.metadata.name):
        tpl = api.sanitize_for_serialization(dep.spec.template)
        entry: dict[str, Any] = {
            "replicas": dep.spec.replicas,
            "template_hash": template_hash(tpl),
            "template": tpl,
        }
        if dep.metadata.name in managed:
            entry["limits"] = _server_limits(tpl)
        deployments[dep.metadata.name] = entry

    captured_at = time.time()
    return {
        "captured_at": captured_at,
        "captured_at_iso": datetime.fromtimestamp(captured_at, timezone.utc).isoformat(),
        "namespace": ns,
        "managed": managed,
        "ob_version": os.environ.get("OB_VERSION", ""),
        "golden_yaml_sha256": sha256_file(GOLDEN_YAML_PATH),
        "contract_sha256": sha256_file(CONTRACT_PATH),
        "git_sha": _git_sha(),
        "template_hash_algo": "sha256(canonical_json(template minus restartedAt annotation))",
        "deployments": deployments,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kubeconfig", default=os.environ.get("KUBE_ADMIN"))
    parser.add_argument("--out", type=Path, default=GOLDEN_LIVE_PATH)
    parser.add_argument("--copy", type=Path, default=SNAPSHOT_BASE_PATH)
    args = parser.parse_args(argv)
    if not args.kubeconfig:
        print("snapshot_golden: set KUBE_ADMIN (source config/cluster.env) or pass --kubeconfig", file=sys.stderr)
        return 2

    try:
        snapshot = build_snapshot(load_contract(), args.kubeconfig)
    except NotCleanError as exc:
        print(f"snapshot_golden: refusing, cluster is not a clean golden apply: {exc}", file=sys.stderr)
        return 1
    except (ApiException, urllib3.exceptions.HTTPError, OSError, subprocess.CalledProcessError,
            KeyError, ValueError) as exc:
        print(f"snapshot_golden: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    text = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    for path in (args.out, args.copy):
        path.write_text(text)
        print(f"wrote {path} ({len(snapshot['deployments'])} deployments)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
