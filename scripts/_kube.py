"""Shared helpers for bootstrap/ops scripts: contract loading and Kubernetes API clients.

Clients are built with library-level retries disabled (CLAUDE.md §4.3.2); callers pass
`_request_timeout=k8s_timeout(contract)` on every call.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from kubernetes import client, config

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = REPO_ROOT / "config" / "contract.yaml"
GOLDEN_YAML_PATH = REPO_ROOT / "k8s" / "boutique" / "golden.yaml"
GOLDEN_LIVE_PATH = REPO_ROOT / "config" / "golden_live.json"
SNAPSHOT_BASE_PATH = REPO_ROOT / "k8s" / "boutique" / "snapshot-base.json"

# Golden hashing lives in env/golden.py (single source for snapshot, preflight and RESTORE).
from env.golden import (  # noqa: F401
    RESTARTED_AT_ANNOTATION,
    strip_restarted_at,
    template_hash,
)


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def k8s_timeout(contract: dict[str, Any]) -> tuple[float, float]:
    connect_s, read_s = contract["telemetry"]["k8s_timeout_s"]
    return (connect_s, read_s)


def api_client(kubeconfig: str | Path) -> client.ApiClient:
    cfg = client.Configuration()
    config.load_kube_config(config_file=str(kubeconfig), client_configuration=cfg)
    cfg.retries = False
    return client.ApiClient(cfg)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rollout_complete(dep: client.V1Deployment) -> bool:
    """CLAUDE.md §5.8 rollout-complete predicate."""
    spec_replicas = dep.spec.replicas or 0
    st = dep.status
    return (
        dep.metadata.generation == st.observed_generation
        and (st.updated_replicas or 0) == spec_replicas
        and (st.available_replicas or 0) == spec_replicas
        and (st.replicas or 0) == spec_replicas
    )


def golden_deployments(path: Path = GOLDEN_YAML_PATH) -> dict[str, int]:
    """Deployment name -> replicas, as declared in the generated golden manifest."""
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
    return {d["metadata"]["name"]: int(d["spec"]["replicas"]) for d in docs if d["kind"] == "Deployment"}
