"""In-process observability: per-stage timings, recent traces, aggregate metrics.

Stages tracked: stt, lm, stock, tts, total (ms). Keeps the last 200 samples per
stage (for p50/p95) and the last 50 turn traces. All in-memory, thread-safe.
"""

import statistics
import threading
import time
from collections import deque
from datetime import datetime

_lock = threading.Lock()
_STAGES = ("stt", "lm", "stock", "tts", "total")
_samples = {k: deque(maxlen=200) for k in _STAGES}
_counts = {"turns": 0, "errors": 0}
_recent = deque(maxlen=50)


def ms() -> float:
    return time.perf_counter() * 1000.0


def _pct(seq, p):
    if not seq:
        return None
    s = sorted(seq)
    return round(s[min(len(s) - 1, int(len(s) * p))], 1)


def record(trace: dict) -> None:
    """Record a completed (or failed) turn trace."""
    trace.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
    with _lock:
        _counts["turns"] += 1
        if trace.get("error"):
            _counts["errors"] += 1
        for k in _STAGES:
            v = trace.get(f"{k}_ms")
            if isinstance(v, (int, float)):
                _samples[k].append(round(v, 1))
        _recent.appendleft(dict(trace))


def snapshot() -> dict:
    with _lock:
        out = {
            "turns": _counts["turns"],
            "errors": _counts["errors"],
            "error_rate": round(_counts["errors"] / _counts["turns"], 3)
            if _counts["turns"]
            else 0.0,
        }
        for k, d in _samples.items():
            out[k] = {
                "n": len(d),
                "avg": round(statistics.fmean(d), 1) if d else None,
                "p50": _pct(d, 0.5),
                "p95": _pct(d, 0.95),
                "max": round(max(d), 1) if d else None,
            }
        out["recent"] = list(_recent)
        return out


def fmt_timings(trace: dict) -> str:
    """Compact 'stt=12,lm=480,stock=90,tts=70,total=660' (skips missing)."""
    parts = []
    for k in _STAGES:
        v = trace.get(f"{k}_ms")
        if isinstance(v, (int, float)):
            parts.append(f"{k}={round(v)}")
    return ",".join(parts)


def log_line(trace: dict) -> str:
    kind = trace.get("kind", "?")
    t = fmt_timings(trace)
    src = trace.get("stock_src")
    src = f" src={src}" if src else ""
    found = trace.get("found")
    found = f" found={'1' if found else '0'}" if found is not None else ""
    items = trace.get("items")
    items = f" items={items}" if items else ""
    err = f" ERROR={trace['error']}" if trace.get("error") else ""
    return f"[trace] {kind} {t}{src}{found}{items}{err}"
