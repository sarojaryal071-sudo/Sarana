"""
core/latency_stats.py — tiny rolling-window latency/depth tracker used by
the web audio transport instrumentation (main.py's _relay_phone_audio()/
_play_audio(), dashboard/server.py's phone_audio_ws/audio_out_ws).

Deliberately minimal: no metrics server, no persistent storage, no
per-sample logging (see the perf-audit task's explicit "do not log every
single audio frame permanently in production" instruction) — just a
bounded deque per metric and cheap avg/p50/p95/max on demand. Safe to
call from a hot audio path: record() is O(1) amortized (deque maxlen
auto-evicts), summary() is O(n log n) on at most `maxlen` samples and is
only ever called periodically (see the callers), never per-chunk.
"""
from collections import deque


class LatencyStats:
    def __init__(self, maxlen: int = 200):
        self._samples = deque(maxlen=maxlen)

    def record(self, value: float) -> None:
        self._samples.append(value)

    def summary(self) -> dict:
        if not self._samples:
            return {"count": 0}
        s = sorted(self._samples)
        n = len(s)
        return {
            "count": n,
            "avg": sum(s) / n,
            "p50": s[n // 2],
            "p95": s[min(n - 1, int(n * 0.95))],
            "max": s[-1],
        }

    def summary_ms(self) -> str:
        """Human-readable one-liner for periodic log lines."""
        d = self.summary()
        if d["count"] == 0:
            return "no samples yet"
        return (
            f"n={d['count']} avg={d['avg']*1000:.1f}ms p50={d['p50']*1000:.1f}ms "
            f"p95={d['p95']*1000:.1f}ms max={d['max']*1000:.1f}ms"
        )
