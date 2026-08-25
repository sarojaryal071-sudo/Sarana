// src/lib/latencyStats.js — tiny rolling-window latency tracker, mirrors
// core/latency_stats.py's Python counterpart. Used by websocket.js's
// browser<->Render RTT probe (item 3 audit). No metrics server, no
// per-sample logging — just a bounded window and cheap avg/p50/p95/max,
// summarized to the console only periodically (see websocket.js).

export class LatencyStats {
  constructor(maxLen = 50) {
    this.maxLen = maxLen;
    this.samples = [];
  }

  record(value) {
    this.samples.push(value);
    if (this.samples.length > this.maxLen) this.samples.shift();
  }

  summary() {
    if (this.samples.length === 0) return null;
    const s = [...this.samples].sort((a, b) => a - b);
    const n = s.length;
    return {
      count: n,
      avg: s.reduce((a, b) => a + b, 0) / n,
      p50: s[Math.floor(n / 2)],
      p95: s[Math.min(n - 1, Math.floor(n * 0.95))],
      max: s[n - 1],
    };
  }
}
