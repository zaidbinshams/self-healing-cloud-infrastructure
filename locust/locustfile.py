"""Online Boutique load + the custom `/tick` window endpoint (CLAUDE.md §5.4, PLAN.md M2).

Task mix and weights are copied from the upstream loadgenerator at the pinned OB_VERSION
(src/loadgenerator/locustfile.py, v0.10.7); faker-generated checkout fields are replaced by
fixed valid values. Wait time and request timeout come from config/contract.yaml.

Every completed request is recorded as (t_B, response_ms, success) with t_B = time.time() on B
at completion. A timeout counts as a failure with response_ms = request_timeout_s * 1000.

GET /tick?from=<unix>&to=<unix> aggregates requests completed in (from, to]:
    {"n": int, "failures": int, "p50_ms": float, "p99_ms": float, "rps": float}
"""

from __future__ import annotations

import datetime
import math
import random
import time
from collections import deque
from pathlib import Path
from typing import Any

import gevent
import yaml
from flask import Response, jsonify, request
from locust.env import Environment

from locust import FastHttpUser, between, events, task

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "config" / "contract.yaml"
_CONTRACT = yaml.safe_load(CONTRACT_PATH.read_text())
WAIT_S: tuple[float, float] = tuple(_CONTRACT["locust"]["wait_s"])
REQUEST_TIMEOUT_S: float = float(_CONTRACT["locust"]["request_timeout_s"])
TIMEOUT_RESPONSE_MS = REQUEST_TIMEOUT_S * 1000
# Keep the window being filled plus the one just closed: /tick is read right after a boundary.
RETENTION_S = 2 * float(_CONTRACT["clock"]["tick_s"])

PRODUCTS = [
    "0PUK6V6EV0", "1YMWWN1N4O", "2ZYFJ3GM2N", "66VCHSJNUP", "6E92ZMYYFZ",
    "9SIQT8TOJO", "L9ECAV7KIM", "LS4PSXUNUM", "OLJCESPC7Z",
]
CURRENCIES = ["EUR", "USD", "JPY", "CAD", "GBP", "TRY"]
CHECKOUT_FORM = {
    "email": "someone@example.com",
    "street_address": "1600 Amphitheatre Parkway",
    "zip_code": "94043",
    "city": "Mountain View",
    "state": "CA",
    "country": "United States",
    "credit_card_number": "4432801561520454",
    "credit_card_cvv": "672",
}


# ----------------------------------------------------------------------------- ring buffer

class RequestLog:
    """Completed requests in B wall-clock order; entries older than RETENTION_S are pruned."""

    def __init__(self, retention_s: float) -> None:
        self.retention_s = retention_s
        self.entries: deque[tuple[float, float, bool]] = deque()
        self.pruned_through = 0.0   # every entry with t_B <= this has been dropped

    def record(self, t_b: float, response_ms: float, success: bool) -> None:
        self.entries.append((t_b, response_ms, success))
        horizon = t_b - self.retention_s
        while self.entries and self.entries[0][0] <= horizon:
            self.pruned_through = self.entries.popleft()[0]

    def window(self, t_from: float, t_to: float) -> list[tuple[float, float, bool]]:
        return [e for e in self.entries if t_from < e[0] <= t_to]


def percentile_ms(sorted_ms: list[float], q: float) -> float:
    """Nearest-rank percentile; 0.0 for an empty window (callers check n == 0)."""
    if not sorted_ms:
        return 0.0
    return sorted_ms[max(0, math.ceil(q * len(sorted_ms)) - 1)]


def aggregate(window: list[tuple[float, float, bool]], duration_s: float) -> dict[str, Any]:
    latencies = sorted(e[1] for e in window)
    return {
        "n": len(window),
        "failures": sum(1 for e in window if not e[2]),
        "p50_ms": percentile_ms(latencies, 0.50),
        "p99_ms": percentile_ms(latencies, 0.99),
        "rps": len(window) / duration_s,
    }


LOG = RequestLog(RETENTION_S)


def _is_timeout(exc: BaseException | None) -> bool:
    return isinstance(exc, (TimeoutError, gevent.Timeout))


@events.request.add_listener
def _on_request(response_time: float, exception: BaseException | None, **_: Any) -> None:
    t_b = time.time()
    if exception is not None and (_is_timeout(exception) or response_time >= TIMEOUT_RESPONSE_MS):
        LOG.record(t_b, TIMEOUT_RESPONSE_MS, False)
    else:
        LOG.record(t_b, float(response_time), exception is None)


@events.init.add_listener
def _on_init(environment: Environment, **_: Any) -> None:
    if environment.web_ui is None:
        raise RuntimeError("locustfile needs the web UI (/tick, /swarm); do not run --headless (CLAUDE.md §8.8)")

    @environment.web_ui.app.route("/tick")
    def tick() -> Response | tuple[Response, int]:
        try:
            t_from = float(request.args["from"])
            t_to = float(request.args["to"])
        except (KeyError, ValueError) as exc:
            return jsonify(error=f"bad query: {type(exc).__name__}: {exc}"), 400
        if not t_to > t_from:
            return jsonify(error="need to > from"), 400
        if t_from < LOG.pruned_through:
            return jsonify(error=f"window starts before retained data ({LOG.pruned_through:.3f})"), 416
        return jsonify(aggregate(LOG.window(t_from, t_to), t_to - t_from))


# ----------------------------------------------------------------------------- user

class BoutiqueUser(FastHttpUser):
    wait_time = between(*WAIT_S)
    network_timeout = REQUEST_TIMEOUT_S
    connection_timeout = REQUEST_TIMEOUT_S

    def on_start(self) -> None:
        self.index()

    @task(1)
    def index(self) -> None:
        self.client.get("/")

    @task(2)
    def set_currency(self) -> None:
        self.client.post("/setCurrency", data={"currency_code": random.choice(CURRENCIES)})

    @task(10)
    def browse_product(self) -> None:
        self.client.get("/product/" + random.choice(PRODUCTS), name="/product/[id]")

    @task(2)
    def add_to_cart(self) -> None:
        product = random.choice(PRODUCTS)
        self.client.get("/product/" + product, name="/product/[id]")
        self.client.post("/cart", data={"product_id": product, "quantity": random.randint(1, 10)})

    @task(3)
    def view_cart(self) -> None:
        self.client.get("/cart")

    @task(1)
    def checkout(self) -> None:
        self.add_to_cart()
        next_year = datetime.datetime.now(tz=datetime.UTC).year + 1
        self.client.post("/cart/checkout", data={
            **CHECKOUT_FORM,
            "credit_card_expiration_month": random.randint(1, 12),
            "credit_card_expiration_year": random.randint(next_year, next_year + 5),
        })
