// src/components/Orb.jsx — closest browser equivalent to ui.py's HudCanvas
// (QPainter-based orb with halo/rings/scanners/particles). Recreated on
// <canvas> with the same color roles and Courier New status text, since a
// literal Qt QPainter scene can't be reproduced in a browser — see Phase 6
// spec section 3 ("create the closest equivalent while preserving the same
// visual language").
import { useEffect, useRef } from "react";

const COLORS = {
  pri: "#00d4ff",
  acc: "#ff6b00",
  muted: "#ff3366",
  green: "#00ff88",
  acc2: "#ffcc00",
  bg: "#00060a",
  ghost: "#001f2e",
  dim: "#0a2433",
};

function hexA(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255,
    g = (n >> 8) & 255,
    b = n & 255;
  return `rgba(${r},${g},${b},${Math.max(0, Math.min(1, a))})`;
}

export default function Orb({ status, assistantName }) {
  const canvasRef = useRef(null);
  const stateRef = useRef({
    tick: 0,
    halo: 55,
    tgtHalo: 55,
    rings: [0, 120, 240],
    scan: 0,
    scan2: 180,
    pulses: [0, 50, 100],
    particles: [],
    lastPulse: 0,
    blink: true,
  });

  // Keep latest props reachable inside the RAF loop without restarting it.
  const propsRef = useRef({ status, assistantName });
  propsRef.current = { status, assistantName };

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    let raf;
    let dpr = window.devicePixelRatio || 1;

    function resize() {
      const rect = canvas.parentElement.getBoundingClientRect();
      dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, rect.width * dpr);
      canvas.height = Math.max(1, rect.height * dpr);
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
    }
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas.parentElement);

    function frame() {
      const s = stateRef.current;
      const { status, assistantName } = propsRef.current;
      const muted = status === "MUTED";
      const speaking = status === "SPEAKING";
      const sleeping = status === "SLEEPING";
      const thinking = status === "THINKING";

      s.tick += 1;
      const tgtHalo = speaking
        ? 145 + Math.random() * 45
        : muted
          ? 15 + Math.random() * 13
          : sleeping
            ? 20 + Math.random() * 8
            : 48 + Math.random() * 20;
      s.halo += (tgtHalo - s.halo) * (speaking ? 0.1 : 0.04);

      const speeds = speaking ? [1.3, -0.9, 2.0] : [0.55, -0.35, 0.9];
      s.rings = s.rings.map((r, i) => (r + speeds[i]) % 360);
      s.scan = (s.scan + (speaking ? 3.0 : 1.3)) % 360;
      s.scan2 = (s.scan2 + (speaking ? -2.0 : -0.75)) % 360;

      const w = canvas.width / dpr;
      const h = canvas.height / dpr;
      const cx = w / 2;
      const cy = h / 2;
      const fw = Math.min(w, h);

      ctx.save();
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = COLORS.bg;
      ctx.fillRect(0, 0, w, h);

      // grid dots
      ctx.fillStyle = COLORS.ghost;
      for (let x = 0; x < w; x += 48) {
        for (let y = 0; y < h; y += 48) {
          ctx.fillRect(x, y, 1, 1);
        }
      }

      const rFace = fw * 0.31;
      const mainColor = muted ? COLORS.muted : COLORS.pri;

      // halo glow rings
      for (let i = 0; i < 10; i++) {
        const r = rFace * (1.8 - i * 0.08);
        const frc = 1 - i / 10;
        ctx.strokeStyle = hexA(mainColor, (s.halo * 0.0034 * frc));
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.stroke();
      }

      // pulse rings (spawn occasionally)
      const lim = fw * 0.74;
      const pulseSpeed = speaking ? 4.2 : 2.0;
      s.pulses = s.pulses.map((r) => r + pulseSpeed).filter((r) => r < lim);
      if (s.pulses.length < 3 && Math.random() < (speaking ? 0.07 : 0.025)) {
        s.pulses.push(0);
      }
      for (const pr of s.pulses) {
        ctx.strokeStyle = hexA(mainColor, Math.max(0, 0.9 * (1 - pr / lim)));
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(cx, cy, pr, 0, Math.PI * 2);
        ctx.stroke();
      }

      // spinning arc rings
      const ringDefs = [
        [0.48, 3, 115, 78],
        [0.4, 2, 78, 55],
        [0.32, 1, 56, 40],
      ];
      ringDefs.forEach(([rFrac, lw, arcLen, gap], idx) => {
        const ringR = fw * rFrac;
        const aVal = Math.max(0, Math.min(1, (s.halo / 255) * (1 - idx * 0.18)));
        ctx.strokeStyle = hexA(mainColor, aVal);
        ctx.lineWidth = lw;
        let angle = s.rings[idx];
        const total = angle + 360;
        while (angle < total) {
          const start = (-angle * Math.PI) / 180;
          const end = (-(angle + arcLen) * Math.PI) / 180;
          ctx.beginPath();
          ctx.arc(cx, cy, ringR, start, end, true);
          ctx.stroke();
          angle += arcLen + gap;
        }
      });

      // scanners
      const sr = fw * 0.5;
      const sa = Math.min(1, (s.halo / 255) * 1.5);
      const ex = speaking ? 75 : 44;
      ctx.strokeStyle = hexA(mainColor, sa);
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.arc(cx, cy, sr, (-s.scan * Math.PI) / 180, (-(s.scan + ex) * Math.PI) / 180, true);
      ctx.stroke();
      ctx.strokeStyle = hexA(COLORS.acc, sa / 2);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(cx, cy, sr, (-s.scan2 * Math.PI) / 180, (-(s.scan2 + ex) * Math.PI) / 180, true);
      ctx.stroke();

      // crosshair + corner brackets (static framing)
      const chR = fw * 0.51;
      const gapH = fw * 0.16;
      ctx.strokeStyle = hexA(mainColor, s.halo * 0.002);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx - chR, cy);
      ctx.lineTo(cx - gapH, cy);
      ctx.moveTo(cx + gapH, cy);
      ctx.lineTo(cx + chR, cy);
      ctx.moveTo(cx, cy - chR);
      ctx.lineTo(cx, cy - gapH);
      ctx.moveTo(cx, cy + gapH);
      ctx.lineTo(cx, cy + chR);
      ctx.stroke();

      const bl = 24;
      ctx.strokeStyle = hexA(mainColor, 0.82);
      ctx.lineWidth = 2;
      const hl = cx - fw / 2,
        hr = cx + fw / 2,
        ht = cy - fw / 2,
        hb = cy + fw / 2;
      [
        [hl, ht, 1, 1],
        [hr, ht, -1, 1],
        [hl, hb, 1, -1],
        [hr, hb, -1, -1],
      ].forEach(([bx, by, dx, dy]) => {
        ctx.beginPath();
        ctx.moveTo(bx, by);
        ctx.lineTo(bx + dx * bl, by);
        ctx.moveTo(bx, by);
        ctx.lineTo(bx, by + dy * bl);
        ctx.stroke();
      });

      // core orb (radial gradient stand-in for the desktop's face image)
      const orbR = fw * 0.27 * (speaking ? 1.08 : 1.0);
      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, orbR);
      if (muted) {
        grad.addColorStop(0, hexA("#c80032", 0.9));
        grad.addColorStop(1, hexA("#c80032", 0));
      } else {
        grad.addColorStop(0, hexA("#003c6e", 0.95));
        grad.addColorStop(1, hexA("#003c6e", 0));
      }
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(cx, cy, orbR, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = hexA(mainColor, Math.min(1, s.halo / 128));
      ctx.font = "bold 13px 'Courier New', monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(assistantName, cx, cy);

      // particles while speaking
      if (speaking && Math.random() < 0.28) {
        const ang = Math.random() * Math.PI * 2;
        const rs = fw * 0.28;
        s.particles.push([
          cx + Math.cos(ang) * rs,
          cy + Math.sin(ang) * rs,
          Math.cos(ang) * (0.9 + Math.random() * 1.5),
          Math.sin(ang) * (0.9 + Math.random() * 1.5) - 0.4,
          1.0,
        ]);
      }
      s.particles = s.particles
        .map((p) => [p[0] + p[2], p[1] + p[3], p[2] * 0.97, p[3] * 0.97, p[4] - 0.028])
        .filter((p) => p[4] > 0);
      ctx.fillStyle = mainColor;
      for (const p of s.particles) {
        ctx.globalAlpha = Math.max(0, p[4]);
        ctx.beginPath();
        ctx.arc(p[0], p[1], 2.5, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      // status text — mirrors ui.py's HudCanvas.paintEvent() label logic
      // exactly (same precedence: muted > speaking > thinking > sleeping >
      // listening), just recreated on canvas — see this file's header.
      s.blink = Math.floor(s.tick / 38) % 2 === 0;
      let label, labelColor;
      if (muted) {
        label = "⊘  MUTED";
        labelColor = COLORS.muted;
      } else if (speaking) {
        label = "●  SPEAKING";
        labelColor = COLORS.acc;
      } else if (thinking) {
        label = (s.blink ? "◈" : "◇") + "  THINKING";
        labelColor = COLORS.acc2;
      } else if (sleeping) {
        label = (s.blink ? "○" : "●") + "  SLEEPING";
        labelColor = COLORS.pri;
      } else {
        label = (s.blink ? "●" : "○") + "  LISTENING";
        labelColor = COLORS.green;
      }
      ctx.fillStyle = labelColor;
      ctx.font = "bold 11px 'Courier New', monospace";
      const labelY = cy + fw * 0.4 + 12;
      ctx.fillText(label, cx, labelY);

      // voice waveform — same bar-graph concept as ui.py's HudCanvas
      // waveform (its own "# waveform" section): a row of vertical bars,
      // random/lively heights ONLY while status is genuinely SPEAKING
      // (real audio actually playing — see App.jsx/AssistantContext.jsx,
      // never inferred from packet arrival), a flat red line while muted,
      // and a slow, subtle idle sine wave otherwise — so the animation is
      // never mistaken for "still speaking" once playback has genuinely
      // stopped.
      const barCount = 36;
      const barW = Math.max(4, Math.min(8, fw / 60));
      const wx0 = cx - (barCount * barW) / 2;
      const wy = labelY + 22;
      for (let i = 0; i < barCount; i++) {
        let hgt, color;
        if (muted) {
          hgt = 2;
          color = COLORS.muted;
        } else if (speaking) {
          // Fresh randomness per bar per frame — a lively, responsive
          // talking effect (matches HudCanvas: random.randint(3, 20)).
          hgt = 3 + Math.random() * 17;
          color = hgt > 12 ? COLORS.pri : hexA(COLORS.pri, 0.55);
        } else {
          hgt = 3 + 2 * Math.sin(s.tick * 0.09 + i * 0.6);
          color = COLORS.dim;
        }
        ctx.fillStyle = color;
        ctx.fillRect(wx0 + i * barW, wy + 10 - hgt / 2, barW - 1, hgt);
      }

      ctx.restore();
      raf = requestAnimationFrame(frame);
    }

    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, []);

  return (
    <div className="orb-stage">
      <canvas ref={canvasRef} />
    </div>
  );
}
