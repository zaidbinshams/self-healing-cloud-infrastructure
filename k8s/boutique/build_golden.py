"""Build the frozen golden Online Boutique manifest from the pinned upstream release.

Transformations (PLAN.md M1):
  * drop the `loadgenerator` Deployment and its ServiceAccount;
  * convert Service `frontend-external` from LoadBalancer to NodePort 30080;
  * set explicit replicas: contract `replicas.base` for managed services, 1 for the rest;
  * apply `golden_overrides` resource overrides from config/contract.yaml;
  * stamp every object with the contract namespace.

Output is deterministic for a given (upstream, contract) pair. The output file is
GENERATED — never hand-edit it; change the inputs and re-run this script.

Usage:
  python k8s/boutique/build_golden.py --upstream k8s/boutique/upstream/kubernetes-manifests.yaml \
      --contract config/contract.yaml --out k8s/boutique/golden.yaml
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import sys
from pathlib import Path
from typing import Any

import yaml

DROPPED_WORKLOAD = "loadgenerator"
FRONTEND_EXTERNAL_SERVICE = "frontend-external"
FRONTEND_NODE_PORT = 30080
DEFAULT_REPLICAS = 1
APP_CONTAINER = "server"
EXPECTED_DEPLOYMENTS = 11  # 10 application services + redis-cart

# golden_overrides key -> (resources section, resource name)
OVERRIDE_KEYS: dict[str, tuple[str, str]] = {
    "cpu_limit": ("limits", "cpu"),
    "memory_limit": ("limits", "memory"),
    "cpu_request": ("requests", "cpu"),
    "memory_request": ("requests", "memory"),
}


class GoldenBuildError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    contract = yaml.safe_load(path.read_text())
    try:
        namespace = contract["cluster"]["namespace"]
        managed = contract["cluster"]["managed"]
        base = contract["replicas"]["base"]
        overrides = contract.get("golden_overrides") or {}
    except (KeyError, TypeError) as exc:
        raise GoldenBuildError(f"contract missing required key: {exc}") from exc
    if not isinstance(namespace, str) or not isinstance(managed, list):
        raise GoldenBuildError("contract cluster.namespace/managed have wrong types")
    if set(base) != set(managed):
        raise GoldenBuildError("contract replicas.base keys must equal cluster.managed")
    if not isinstance(overrides, dict):
        raise GoldenBuildError("contract golden_overrides must be a mapping")
    return {"namespace": namespace, "managed": managed, "base": base, "overrides": overrides}


def _is_dropped(doc: dict[str, Any]) -> bool:
    return doc["kind"] in ("Deployment", "ServiceAccount") and doc["metadata"]["name"] == DROPPED_WORKLOAD


def _app_container(dep: dict[str, Any]) -> dict[str, Any]:
    containers = dep["spec"]["template"]["spec"]["containers"]
    matches = [c for c in containers if c["name"] == APP_CONTAINER]
    if len(matches) != 1:
        raise GoldenBuildError(f"{dep['metadata']['name']}: expected exactly one container named {APP_CONTAINER!r}")
    return matches[0]


def _apply_overrides(dep: dict[str, Any], spec: dict[str, Any]) -> None:
    unknown = set(spec) - set(OVERRIDE_KEYS)
    if unknown:
        raise GoldenBuildError(f"{dep['metadata']['name']}: unknown golden_overrides keys {sorted(unknown)}")
    resources = _app_container(dep).setdefault("resources", {})
    for key, value in spec.items():
        section, resource = OVERRIDE_KEYS[key]
        resources.setdefault(section, {})[resource] = str(value)


def transform(docs: list[dict[str, Any]], contract: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_frontend_external = False
    deployments: set[str] = set()

    for original in docs:
        if _is_dropped(original):
            continue
        doc = copy.deepcopy(original)
        name = doc["metadata"]["name"]
        doc["metadata"]["namespace"] = contract["namespace"]

        if doc["kind"] == "Deployment":
            deployments.add(name)
            doc["spec"]["replicas"] = int(contract["base"].get(name, DEFAULT_REPLICAS))
            if name in contract["managed"]:
                _app_container(doc)  # managed services must expose the `server` container
            if name in contract["overrides"]:
                _apply_overrides(doc, contract["overrides"][name])

        elif doc["kind"] == "Service" and name == FRONTEND_EXTERNAL_SERVICE:
            seen_frontend_external = True
            doc["spec"]["type"] = "NodePort"
            ports = doc["spec"]["ports"]
            if len(ports) != 1:
                raise GoldenBuildError(f"{name}: expected exactly one port, found {len(ports)}")
            ports[0]["nodePort"] = FRONTEND_NODE_PORT

        out.append(doc)

    if not seen_frontend_external:
        raise GoldenBuildError(f"upstream has no Service {FRONTEND_EXTERNAL_SERVICE!r}")
    missing_managed = set(contract["managed"]) - deployments
    if missing_managed:
        raise GoldenBuildError(f"managed deployments missing upstream: {sorted(missing_managed)}")
    unknown_overrides = set(contract["overrides"]) - deployments
    if unknown_overrides:
        raise GoldenBuildError(f"golden_overrides for unknown deployments: {sorted(unknown_overrides)}")
    if len(deployments) != EXPECTED_DEPLOYMENTS:
        raise GoldenBuildError(f"expected {EXPECTED_DEPLOYMENTS} deployments, got {len(deployments)}: {sorted(deployments)}")
    return out


def render(docs: list[dict[str, Any]], upstream: Path, contract_path: Path) -> str:
    header = (
        "# GENERATED by k8s/boutique/build_golden.py — DO NOT EDIT.\n"
        f"# upstream: {upstream.as_posix()} sha256={_sha256(upstream)}\n"
        f"# contract: {contract_path.as_posix()} sha256={_sha256(contract_path)}\n"
    )
    body = "---\n".join(yaml.safe_dump(d, sort_keys=False, default_flow_style=False) for d in docs)
    return header + "---\n" + body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        contract = load_contract(args.contract)
        docs = [d for d in yaml.safe_load_all(args.upstream.read_text()) if d]
        golden = transform(docs, contract)
    except (OSError, yaml.YAMLError, GoldenBuildError) as exc:
        print(f"build_golden: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    args.out.write_text(render(golden, args.upstream, args.contract))
    n_dep = sum(1 for d in golden if d["kind"] == "Deployment")
    print(f"wrote {args.out} ({len(golden)} objects, {n_dep} deployments)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
