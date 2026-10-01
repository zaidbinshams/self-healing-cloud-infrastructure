"""PromQL text must match CLAUDE.md §5.4 verbatim."""

from scripts.promq import build_queries

DEP = r'"deployment", "$1", "pod", "^(.+)-[a-z0-9]{6,10}-[a-z0-9]{5}$"'
SEL = '{namespace="boutique",container="server"}'


def test_queries_verbatim():
    q = build_queries("30s")
    assert q["cpu"] == (f"sum by (deployment) (label_replace(rate(container_cpu_usage_seconds_total{SEL}[30s]), {DEP}))")
    assert q["mem"] == f"sum by (deployment) (label_replace(container_memory_working_set_bytes{SEL}, {DEP}))"
    num, den = (part.strip() for part in q["thr"].split("\n      / "))
    assert num == f"sum by (deployment) (label_replace(rate(container_cpu_cfs_throttled_periods_total{SEL}[30s]), {DEP}))"
    assert den == f"sum by (deployment) (label_replace(rate(container_cpu_cfs_periods_total{SEL}[30s]), {DEP}))"
