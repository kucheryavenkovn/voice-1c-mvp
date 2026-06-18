"""Observability: per-stage timings header, /metrics aggregation, /monitor page, error counting."""


def test_ask_text_has_timings(gw):
    r = gw.client.post("/ask-text", json={"text": "сколько молока?"})
    assert r.status_code == 200
    t = r.headers["X-Timings"]
    assert "lm=" in t and "tts=" in t and "total=" in t


def test_transcribe_has_stt_timings(gw):
    r = gw.client.post("/transcribe", files={"file": ("a.webm", b"x", "audio/webm")})
    assert "stt=" in r.headers["X-Timings"]


def test_metrics_accumulates(gw):
    gw.client.post("/ask-text", json={"text": "сколько молока?"})
    m = gw.client.get("/metrics").json()
    assert m["turns"] >= 1
    assert m["lm"]["n"] >= 1 and m["tts"]["n"] >= 1
    assert m["recent"] and m["recent"][0]["ts"]  # timestamp present
    for stage in ("stt", "lm", "stock", "tts", "total"):
        assert set(m[stage]) >= {"n", "avg", "p50", "p95", "max"}


def test_monitor_page(gw):
    r = gw.client.get("/monitor")
    assert r.status_code == 200
    assert "Этапы" in r.text


def test_error_recorded_in_metrics(gw):
    gw.tts_fail = True
    gw.client.post("/ask-text", json={"text": "сколько молока?"})
    gw.tts_fail = False
    assert gw.client.get("/metrics").json()["errors"] >= 1
