"""In-process observability: per-stage timings, recent traces, aggregate metrics.

Stages tracked: stt, lm, stock, tts, total (ms). Keeps the last 200 samples per
stage (for p50/p95) and the last 50 turn traces. All in-memory, thread-safe.
"""

import statistics
import threading
import time
from collections import deque
from datetime import datetime

# START_MODULE_CONTRACT
#   PURPOSE: In-process observability: stage timings, traces, LM token metrics.
#   SCOPE: aggregation (avg/p50/p95/max), recent traces, LM tokens/cost/tps.
#   DEPENDS: none (stdlib only)
#   LINKS: M-OBSERVABILITY, V-M-OBSERVABILITY, DF-OBSERVABILITY
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ms - monotonic clock in ms
#   record - accumulate turn trace
#   record_lm - accumulate LM token/cost/tps stats
#   snapshot - aggregate snapshot for /metrics and monitor UI
#   fmt_timings - compact timings string
#   log_line - trace log line
# END_MODULE_MAP

_lock = threading.Lock()
_STAGES = ("stt", "lm", "stock", "tts", "total")
_samples = {k: deque(maxlen=200) for k in _STAGES}
_counts = {"turns": 0, "errors": 0}
_recent = deque(maxlen=50)
_lm = {
    "model": None,
    "calls": 0,
    "prompt": 0,
    "completion": 0,
    "cached": 0,
    "cost": 0.0,
    "tps": deque(maxlen=200),
}


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


# START_CONTRACT: record_lm
#   PURPOSE: Накопить LM-статистику вызова (токены, кэш, скорость, цена).
#   INPUTS: { model, prompt_tokens, completion_tokens, cached_tokens, lm_ms, cost }
#   OUTPUTS: { None }
#   LINKS: M-OBSERVABILITY, DF-OBSERVABILITY
# END_CONTRACT: record_lm
def record_lm(
    model: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
    lm_ms: float,
    cost: float = 0.0,
) -> None:
    """Накопить LM-статистику одного вызова: токены, кэш, скорость, цена."""
    with _lock:
        _lm["model"] = model or _lm["model"]
        _lm["calls"] += 1
        _lm["prompt"] += int(prompt_tokens or 0)
        _lm["completion"] += int(completion_tokens or 0)
        _lm["cached"] += int(cached_tokens or 0)
        _lm["cost"] += float(cost or 0.0)
        if lm_ms and completion_tokens:
            _lm["tps"].append(round(completion_tokens / (lm_ms / 1000.0), 1))


# START_CONTRACT: snapshot
#   PURPOSE: Агрегированный срез метрик для /metrics и monitor UI.
#   INPUTS: {}
#   OUTPUTS: { dict: stages, lm_tokens, recent, counts }
#   LINKS: M-OBSERVABILITY
# END_CONTRACT: snapshot
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
        tps = sorted(_lm["tps"])
        out["lm_tokens"] = {
            "model": _lm["model"],
            "calls": _lm["calls"],
            "prompt_tokens": _lm["prompt"],
            "completion_tokens": _lm["completion"],
            "cached_tokens": _lm["cached"],
            "cost": round(_lm["cost"], 4) if _lm["cost"] else None,
            "tps_avg": round(statistics.fmean(tps), 1) if tps else None,
            "tps_max": max(tps) if tps else None,
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
    # GRACE log-маркер: [Component][function][BLOCK_*] (стабильные имена)
    marker = ""
    if trace.get("component"):
        marker = f" [{trace['component']}][{trace.get('function', '?')}]{trace.get('block', '')}"
    return f"[trace]{marker} {kind} {t}{src}{found}{items}{err}"


# GRACE: стабильный публичный экспорт (для точной проверки поверхности)
__all__ = [
    "fmt_timings",
    "log_line",
    "ms",
    "record",
    "record_lm",
    "snapshot",
]
