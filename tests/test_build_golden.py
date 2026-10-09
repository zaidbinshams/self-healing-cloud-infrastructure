"""k8s/boutique/build_golden.py golden_overrides handling (pure; hand-written manifests)."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "build_golden", Path(__file__).resolve().parent.parent / "k8s" / "boutique" / "build_golden.py")
bg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bg)

DIGEST = "redis:8.10.2-alpine@sha256:" + "a" * 64


def deployment(name: str, containers: list[str]) -> dict:
    return {"kind": "Deployment", "metadata": {"name": name},
            "spec": {"template": {"spec": {"containers": [
                {"name": c, "image": f"{c}:old", "resources": {"limits": {"cpu": "200m"}}} for c in containers]}}}}


def test_image_override_on_single_non_server_container():
    dep = deployment("redis-cart", ["redis"])
    bg._apply_overrides(dep, {"image": DIGEST})
    assert dep["spec"]["template"]["spec"]["containers"][0]["image"] == DIGEST


def test_resource_override_targets_server_container():
    dep = deployment("frontend", ["server", "sidecar"])
    before = copy.deepcopy(dep["spec"]["template"]["spec"]["containers"][1])
    bg._apply_overrides(dep, {"cpu_limit": "400m"})
    server, sidecar = dep["spec"]["template"]["spec"]["containers"]
    assert server["resources"]["limits"]["cpu"] == "400m" and sidecar == before


@pytest.mark.parametrize("spec", [{"image": "redis:alpine"}, {"gpu": "1"},
                                  {"env.EXTRA_LATENCY": "1s"}, {"env.1BAD": "x"}])
def test_bad_overrides_rejected(spec):
    with pytest.raises(bg.GoldenBuildError):
        bg._apply_overrides(deployment("redis-cart", ["redis"]), spec)


def test_env_override_sets_or_replaces_variable():
    dep = deployment("cartservice", ["server"])
    dep["spec"]["template"]["spec"]["containers"][0]["env"] = [{"name": "REDIS_ADDR", "value": "redis-cart:6379"},
                                                               {"name": "KNOB", "value": "old"}]
    bg._apply_overrides(dep, {"env.KNOB": "new", "env.DOTNET_ThreadPool_ForceMinWorkerThreads": "0x20"})
    env = {e["name"]: e["value"] for e in dep["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env == {"REDIS_ADDR": "redis-cart:6379", "KNOB": "new", "DOTNET_ThreadPool_ForceMinWorkerThreads": "0x20"}
