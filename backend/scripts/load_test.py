"""Real load-testing tool against the already-running BODP API (Phase 10
hardening task 3) — commits the Gunicorn/httpx concurrent-load methodology
this project's own Visualize Performance work already proved out
(scratchpad-only during that phase) as a real, reusable, parameterized
tool instead of re-deriving it each time.

A pure external HTTP client, deliberately: no app.* imports, no direct DB
access — it exercises the API exactly the way a real browser/script
would, over the network, against whatever URL it's pointed at (the
isolated dev stack, a staging environment, or production). The one
prerequisite is a verified user to authenticate as; run
`python -m app.scripts.seed_load_test_user` once first against the target
environment's database (a separate, DB-touching setup step, deliberately
NOT part of this tool — see that script's own docstring).

Usage:
    python scripts/load_test.py --scenario mixed --concurrency 500 --duration 60 \\
        --base-url http://localhost:8000 --report out.json

Scenarios:
    catalog_search      Public GET /catalog/search with varied query params.
    request_submission  Authenticated POST /requests — exercises the
                         RATE_LIMIT_MUTATIONS limiter deliberately; a
                         nonzero 429 rate at high concurrency is expected,
                         correct behavior, reported as a first-class metric,
                         not hidden as an error.
    visualization       POST /visualize/{timeseries,spatial,comparison,
                         statistics,profiles} — the QUERY_CONCURRENCY_LIMIT_
                         PER_WORKER-gated endpoints, varied dataset/
                         parameter/bbox combinations, polls a queued job_id
                         to completion when the sync fast path isn't taken.
    mixed                A weighted blend (70% catalog, 20% visualization,
                         10% request submission) approximating realistic
                         traffic shape — the one scenario that actually
                         answers the Master Plan's "~500 concurrent
                         simulated users" quality check, since real traffic
                         is never single-endpoint.
"""

import argparse
import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

_SCENARIO_DATA_PATH = Path(__file__).parent / "load_test_scenarios" / "datasets.json"

_POLL_INTERVAL_SECONDS = 1.0
_POLL_MAX_ATTEMPTS = 30

# Only these three visualize endpoints ever return status="queued" (see
# app/schemas/visualize.py) — timeseries and profiles are always
# synchronous, so polling them would just 404 against a job_id that was
# never returned.
_POLLABLE_ENDPOINTS = ("spatial", "comparison", "statistics")


@dataclass
class RequestOutcome:
    scenario: str
    status_code: int
    latency_seconds: float
    error: str | None = None


@dataclass
class LoadTestReport:
    scenario: str
    concurrency: int
    duration_seconds: float
    ramp_up_seconds: float
    started_at: float
    outcomes: list[RequestOutcome] = field(default_factory=list)

    def summary(self) -> dict:
        total = len(self.outcomes)
        if total == 0:
            return {"total_requests": 0}

        latencies = sorted(o.latency_seconds for o in self.outcomes)

        def pct(p: float) -> float:
            idx = min(int(len(latencies) * p), len(latencies) - 1)
            return round(latencies[idx], 4)

        status_counts: dict[str, int] = {}
        for o in self.outcomes:
            key = str(o.status_code) if o.status_code else "connection_error"
            status_counts[key] = status_counts.get(key, 0) + 1

        errors_5xx = sum(1 for o in self.outcomes if o.status_code >= 500 or o.status_code == 0)
        rate_429 = sum(1 for o in self.outcomes if o.status_code == 429)
        wall_seconds = max(o.latency_seconds for o in self.outcomes) if self.outcomes else 0

        return {
            "scenario": self.scenario,
            "concurrency": self.concurrency,
            "configured_duration_seconds": self.duration_seconds,
            "ramp_up_seconds": self.ramp_up_seconds,
            "total_requests": total,
            "throughput_rps": round(total / self.duration_seconds, 2) if self.duration_seconds > 0 else None,
            "latency_p50_seconds": pct(0.50),
            "latency_p95_seconds": pct(0.95),
            "latency_p99_seconds": pct(0.99),
            "status_code_counts": status_counts,
            "rate_429_count": rate_429,
            "rate_429_pct": round(100 * rate_429 / total, 2),
            "error_5xx_or_connection_count": errors_5xx,
            "error_5xx_or_connection_pct": round(100 * errors_5xx / total, 2),
        }


def _load_scenario_data() -> dict:
    return json.loads(_SCENARIO_DATA_PATH.read_text())


async def _login(client: httpx.AsyncClient, base_url: str, email: str, password: str) -> str:
    r = await client.post(f"{base_url}/api/v1/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["tokens"]["access_token"]


async def _timed_request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> RequestOutcome:
    start = time.monotonic()
    try:
        r = await client.request(method, url, **kwargs)
        latency = time.monotonic() - start
        return RequestOutcome(scenario="", status_code=r.status_code, latency_seconds=latency)
    except httpx.HTTPError as exc:
        latency = time.monotonic() - start
        return RequestOutcome(scenario="", status_code=0, latency_seconds=latency, error=str(exc))


async def _poll_job(client: httpx.AsyncClient, base_url: str, endpoint: str, job_id: str) -> None:
    for _ in range(_POLL_MAX_ATTEMPTS):
        r = await client.get(f"{base_url}/api/v1/visualize/{endpoint}/{job_id}")
        if r.status_code == 200 and r.json().get("status") in ("complete", "failed"):
            return
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def _catalog_search_request(client: httpx.AsyncClient, base_url: str, data: dict) -> RequestOutcome:
    params = {}
    if random.random() < 0.6:
        params["search"] = random.choice(data["catalog_search_terms"])
    if random.random() < 0.3:
        params["category"] = random.choice(data["catalog_categories"])
    outcome = await _timed_request(client, "GET", f"{base_url}/api/v1/catalog/search", params=params)
    outcome.scenario = "catalog_search"
    return outcome


async def _visualization_request_with_poll(client: httpx.AsyncClient, base_url: str, data: dict) -> RequestOutcome:
    target = random.choice(data["visualization_targets"])
    parameter = random.choice(target["parameters"])
    endpoint = random.choices(
        ["timeseries", "spatial", "comparison", "statistics", "profiles"],
        weights=[30, 20, 15, 25, 10],
        k=1,
    )[0]

    body: dict = {"dataset_id": target["dataset_id"]}
    if endpoint == "comparison":
        params_pool = target["parameters"]
        body["parameters"] = params_pool[:2] if len(params_pool) >= 2 else params_pool * 2
    elif endpoint == "spatial":
        body["parameter"] = parameter
        body["bounds"] = data["spatial_bounds"]
    else:
        body["parameter"] = parameter

    start = time.monotonic()
    try:
        r = await client.post(f"{base_url}/api/v1/visualize/{endpoint}", json=body)
        if r.status_code == 200 and endpoint in _POLLABLE_ENDPOINTS and r.json().get("status") == "queued":
            job_id = r.json()["job_id"]
            await _poll_job(client, base_url, endpoint, job_id)
        latency = time.monotonic() - start
        return RequestOutcome(scenario=f"visualization:{endpoint}", status_code=r.status_code, latency_seconds=latency)
    except httpx.HTTPError as exc:
        latency = time.monotonic() - start
        return RequestOutcome(
            scenario=f"visualization:{endpoint}", status_code=0, latency_seconds=latency, error=str(exc)
        )


async def _request_submission_request(
    client: httpx.AsyncClient, base_url: str, data: dict, token: str
) -> RequestOutcome:
    target = random.choice(data["request_submission_targets"])
    justification = (
        f"Load test justification for {target['code']} — synthetic traffic generated by "
        f"backend/scripts/load_test.py (Phase 10.6), not a real research request. Padding "
        f"text to satisfy the 50-character minimum length validation rule."
    )
    outcome = await _timed_request(
        client,
        "POST",
        f"{base_url}/api/v1/requests",
        data={"dataset_id": target["dataset_id"], "justification": justification},
        headers={"Authorization": f"Bearer {token}"},
    )
    outcome.scenario = "request_submission"
    return outcome


async def _run_worker(
    scenario: str,
    base_url: str,
    data: dict,
    token: str | None,
    end_time: float,
    outcomes: list[RequestOutcome],
    lock: asyncio.Lock,
) -> None:
    async with httpx.AsyncClient(timeout=120.0) as client:
        while time.monotonic() < end_time:
            if scenario == "catalog_search":
                outcome = await _catalog_search_request(client, base_url, data)
            elif scenario == "visualization":
                outcome = await _visualization_request_with_poll(client, base_url, data)
            elif scenario == "request_submission":
                outcome = await _request_submission_request(client, base_url, data, token)
            elif scenario == "mixed":
                roll = random.random()
                if roll < 0.70:
                    outcome = await _catalog_search_request(client, base_url, data)
                elif roll < 0.90:
                    outcome = await _visualization_request_with_poll(client, base_url, data)
                else:
                    outcome = await _request_submission_request(client, base_url, data, token)
            else:
                raise ValueError(f"Unknown scenario: {scenario}")

            async with lock:
                outcomes.append(outcome)


async def run_load_test(
    *,
    base_url: str,
    scenario: str,
    concurrency: int,
    duration_seconds: float,
    ramp_up_seconds: float,
    email: str,
    password: str,
) -> LoadTestReport:
    data = _load_scenario_data()

    token = None
    if scenario in ("request_submission", "mixed"):
        async with httpx.AsyncClient(timeout=30.0) as login_client:
            token = await _login(login_client, base_url, email, password)

    outcomes: list[RequestOutcome] = []
    lock = asyncio.Lock()
    started_at = time.monotonic()
    end_time = started_at + duration_seconds

    tasks = []
    per_worker_delay = ramp_up_seconds / concurrency if concurrency > 0 else 0
    for i in range(concurrency):
        if per_worker_delay > 0:
            await asyncio.sleep(per_worker_delay)
        tasks.append(
            asyncio.create_task(_run_worker(scenario, base_url, data, token, end_time, outcomes, lock))
        )

    await asyncio.gather(*tasks)

    return LoadTestReport(
        scenario=scenario,
        concurrency=concurrency,
        duration_seconds=duration_seconds,
        ramp_up_seconds=ramp_up_seconds,
        started_at=started_at,
        outcomes=outcomes,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--scenario", choices=["catalog_search", "request_submission", "visualization", "mixed"], required=True)
    parser.add_argument("--concurrency", type=int, default=200)
    parser.add_argument("--duration", type=float, default=60.0, help="Test duration in seconds")
    parser.add_argument("--ramp-up", type=float, default=10.0, help="Seconds to spread worker startup over")
    parser.add_argument("--email", default="load-test@example.com")
    parser.add_argument("--password", default="LoadTest123x")
    parser.add_argument("--report", default=None, help="Path to write the JSON report to")
    args = parser.parse_args()

    report = asyncio.run(
        run_load_test(
            base_url=args.base_url,
            scenario=args.scenario,
            concurrency=args.concurrency,
            duration_seconds=args.duration,
            ramp_up_seconds=args.ramp_up,
            email=args.email,
            password=args.password,
        )
    )

    summary = report.summary()
    print(json.dumps(summary, indent=2))

    if args.report:
        Path(args.report).write_text(json.dumps(summary, indent=2))
        print(f"\nReport written to {args.report}")


if __name__ == "__main__":
    main()
