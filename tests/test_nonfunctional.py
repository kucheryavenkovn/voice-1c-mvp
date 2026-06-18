"""Non-functional tests: graceful degradation under timeouts and concurrency."""

import asyncio
import time
from urllib.parse import unquote

import httpx


def test_onec_timeout_falls_back_to_mock(gw):
    """1C read-timeout → gateway must not hang; falls back to mock-api."""
    gw.onec_timeout = True
    t0 = time.monotonic()
    r = gw.client.post("/ask-text", json={"text": "сколько молока?"})
    elapsed = time.monotonic() - t0
    assert r.status_code == 200
    assert "42" in unquote(r.headers["X-Answer"])  # mock fallback answer
    assert elapsed < 5.0, f"request hung for {elapsed:.1f}s"


def test_lm_timeout_degrades_gracefully(gw):
    """LM read-timeout → intent becomes None → help/unknown answer, still 200."""
    gw.lm_timeout = True
    r = gw.client.post("/ask-text", json={"text": "сколько молока?"})
    assert r.status_code == 200
    # no intent → build_answer returns the help line mentioning остатки
    assert "остатк" in unquote(r.headers["X-Answer"])


def test_concurrent_ask_text_all_succeed(gw):
    """20 concurrent turns must all succeed (no shared-state / threadpool bug)."""
    import app

    async def run():
        transport = httpx.ASGITransport(app=app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await asyncio.gather(
                *[c.post("/ask-text", json={"text": "x"}) for _ in range(20)]
            )

    responses = asyncio.run(run())
    assert len(responses) == 20
    assert all(r.status_code == 200 for r in responses)
