"""Golden state: the RESTORE / reset() source of truth (CLAUDE.md §5.2, §5.9). Pure.

`config/golden_live.json` holds the API-defaulted pod templates captured by
scripts/snapshot_golden.py right after a verified clean apply. Live templates are compared
against it by `template_hash`, which ignores the rollout-restart annotation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from env.contract import GOLDEN_LIVE_PATH, Contract, ContractError

RESTARTED_AT_ANNOTATION = "kubectl.kubernetes.io/restartedAt"


def strip_restarted_at(template: dict[str, Any]) -> dict[str, Any]:
    """A copy of a pod template (API JSON form) without the restartedAt annotation."""
    tpl = json.loads(json.dumps(template))
    meta = tpl.get("metadata") or {}
    annotations = meta.get("annotations") or {}
    annotations.pop(RESTARTED_AT_ANNOTATION, None)
    if annotations:
        meta["annotations"] = annotations
    else:
        meta.pop("annotations", None)
    return tpl


def template_hash(template: dict[str, Any]) -> str:
    """sha256 over canonical JSON of the template, ignoring restartedAt (CLAUDE.md §5.2)."""
    canonical = json.dumps(strip_restarted_at(template), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class GoldenDeployment:
    template: dict[str, Any]
    template_hash: str
    replicas: int


def load_golden(contract: Contract, path: Path = GOLDEN_LIVE_PATH) -> dict[str, GoldenDeployment]:
    """Golden template, hash and replicas for each managed deployment; validates the stored hashes."""
    try:
        snap = json.loads(path.read_text())
        entries = {d: snap["deployments"][d] for d in contract.cluster.managed}
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ContractError(f"cannot read golden state from {path}: {type(exc).__name__}: {exc}") from exc
    out: dict[str, GoldenDeployment] = {}
    for d, e in entries.items():
        h = template_hash(e["template"])
        if h != e["template_hash"]:
            raise ContractError(f"{path}: stored template_hash for {d} does not match its template")
        if int(e["replicas"]) != contract.replicas.base[d]:
            raise ContractError(f"{path}: golden replicas for {d} ({e['replicas']}) != contract base")
        out[d] = GoldenDeployment(e["template"], h, int(e["replicas"]))
    return out
