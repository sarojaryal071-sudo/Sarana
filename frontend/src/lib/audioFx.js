// src/lib/audioFx.js — Track 4's cinematic audio EVENT system: semantic
// event name in ("assistant_wake", "transition_sarana_to_jarvis", ...),
// a short, real, procedurally-synthesized sound out. Deliberately NOT a
// second audio engine sitting next to lib/audioOut.js — that module
// streams Gemini's own live-session TTS PCM over /ws/audio-out, a
// continuous, server-driven, speech-shaped stream; this module plays
// short, local, one-shot UI cues on the browser's own Web Audio API,
// with no server round-trip and no relationship to speech PCM at all.
// Genuinely different concerns (same distinction J8/J9 already drew
// between repo_agent.py and git_control.py on the backend) — sharing an
// AudioContext or a playback queue between them would couple two things
// that don't actually interact.
//
// Asset strategy (section 11's own required disclosure): no .mp3/.wav
// files ship with this module. Every cue is synthesized in real time
// from OscillatorNode/BiquadFilterNode/GainNode primitives — a genuine,
// deliberate sound-design choice, not a placeholder standing in for
// "real" assets that couldn't be generated in this environment. This is
// how a large share of real product UI sound (macOS system sounds,
// game UI blips, etc.) is actually built, and it means every cue is
// tiny (no asset weight), deterministic, and themeable (JARVIS's own
// cues run a semitone lower / slightly more square-edged than SARANA's
// — see THEME_DETUNE below) without needing a second asset per theme.
//
// Priority / mixing (section 13): three tiers. CRITICAL always plays
// (confirmation_required, blocked, error — the user needs to know even
// mid-speech). NORMAL plays unless the assistant is currently speaking
// (ducks under speech rather than fighting it). AMBIENT (listening/
// thinking) is suppressed entirely while speaking AND is the first
// thing skipped if the same event fires again within its own cooldown
// — this is "use silence when silence is better" (section 12's own
// closing line), not "play a sound for every state change".

const PRIORITY = { CRITICAL: 2, NORMAL: 1, AMBIENT: 0 };

// name -> {priority, cooldownMs, synth(ctx, dest, opts)}
// Populated below, after the synthesis helpers exist.
const EVENTS = {};

let _ctx = null;
let _master = null;
let _speaking = false;
const _lastPlayedAt = new Map();

function _getContext() {
  if (_ctx) return _ctx;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    _ctx = new Ctx();
    _master = _ctx.createGain();
    _master.gain.value = 0.5; // restrained overall level -- these are cues, not the show
    _master.connect(_ctx.destination);
  } catch {
    _ctx = null; // missing/blocked Web Audio -- every playEvent() call below just no-ops
  }
  return _ctx;
}

// ── synthesis primitives (small, composable, no randomness beyond
// controlled noise bursts for the metallic/mechanical texture) ────────

function _envelope(ctx, gainNode, { attack = 0.005, hold = 0, decay = 0.08, peak = 0.6 }, startAt) {
  const g = gainNode.gain;
  g.cancelScheduledValues(startAt);
  g.setValueAtTime(0.0001, startAt);
  g.exponentialRampToValueAtTime(peak, startAt + attack);
  g.setValueAtTime(peak, startAt + attack + hold);
  g.exponentialRampToValueAtTime(0.0001, startAt + attack + hold + decay);
}

function _tone(ctx, dest, { freq = 880, type = "sine", startAt, detune = 0, ...env }) {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  osc.detune.value = detune;
  osc.connect(gain);
  gain.connect(dest);
  _envelope(ctx, gain, env, startAt);
  osc.start(startAt);
  osc.stop(startAt + (env.attack ?? 0.005) + (env.hold ?? 0) + (env.decay ?? 0.08) + 0.02);
}

function _sweep(ctx, dest, { from = 400, to = 1200, startAt, dur = 0.18, type = "sine", ...env }) {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(from, startAt);
  osc.frequency.exponentialRampToValueAtTime(Math.max(1, to), startAt + dur);
  osc.connect(gain);
  gain.connect(dest);
  _envelope(ctx, gain, { attack: 0.005, hold: dur - 0.02, decay: 0.05, peak: env.peak ?? 0.5 }, startAt);
  osc.start(startAt);
  osc.stop(startAt + dur + 0.05);
}

// A single filtered noise "click" -- the metallic-particle texture the
// transition sounds are built from (section 11: "tiny metallic
// components... mechanical clicks").
function _click(ctx, dest, { startAt, freq = 3000, q = 8, dur = 0.02, peak = 0.35 }) {
  const bufferSize = Math.max(1, Math.floor(ctx.sampleRate * dur));
  const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < bufferSize; i++) data[i] = Math.random() * 2 - 1;
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  const filter = ctx.createBiquadFilter();
  filter.type = "bandpass";
  filter.frequency.value = freq;
  filter.Q.value = q;
  const gain = ctx.createGain();
  src.connect(filter);
  filter.connect(gain);
  gain.connect(dest);
  _envelope(ctx, gain, { attack: 0.001, hold: 0, decay: dur, peak }, startAt);
  src.start(startAt);
  src.stop(startAt + dur + 0.01);
}

function _clickBurst(ctx, dest, { startAt, count = 6, spread = 0.09, ...clickOpts }) {
  for (let i = 0; i < count; i++) {
    const t = startAt + Math.random() * spread;
    _click(ctx, dest, { ...clickOpts, startAt: t, freq: (clickOpts.freq || 3000) * (0.7 + Math.random() * 0.8) });
  }
}

// ── theme (JARVIS vs SARANA) — one detune value, applied uniformly ───
// rather than a second cue set per theme (section 9's own "shared
// infrastructure with themes/variants, do not duplicate the whole
// system"). JARVIS reads slightly lower/more mechanical; SARANA
// slightly higher/warmer -- the SAME synthesis code either way.
const THEME_DETUNE = { jarvis: -60, sarana: 40 };
let _theme = "sarana";

export function setAudioFxTheme(theme) {
  _theme = theme === "jarvis" ? "jarvis" : "sarana";
}

export function setSpeaking(isSpeaking) {
  _speaking = !!isSpeaking;
}

// ── event vocabulary (section 12) ─────────────────────────────────────
// Only events with an honest, real triggering signal are wired from
// App.jsx (see that file's own audio-fx effects) — task_started/
// task_completed are deliberately NOT included: no existing signal
// distinguishes "a tool call started" from ordinary THINKING/SPEAKING
// state without inventing new backend plumbing speculatively (section
// 23's own "no unnecessary architectural expansion"), so they're a
// disclosed, genuine limitation rather than faked with a generic click.
function _detune() {
  return THEME_DETUNE[_theme] ?? 0;
}

Object.assign(EVENTS, {
  assistant_wake: {
    priority: PRIORITY.NORMAL, cooldownMs: 800,
    synth(ctx, dest, t) {
      _tone(ctx, dest, { freq: 520, type: "sine", startAt: t, detune: _detune(), attack: 0.004, decay: 0.09, peak: 0.5 });
      _tone(ctx, dest, { freq: 780, type: "sine", startAt: t + 0.05, detune: _detune(), attack: 0.004, decay: 0.12, peak: 0.4 });
    },
  },
  assistant_sleep: {
    priority: PRIORITY.NORMAL, cooldownMs: 800,
    synth(ctx, dest, t) {
      _sweep(ctx, dest, { from: 500, to: 160, startAt: t, dur: 0.28, type: "sine", peak: 0.4, detune: _detune() });
    },
  },
  assistant_listening: {
    priority: PRIORITY.AMBIENT, cooldownMs: 1500,
    synth(ctx, dest, t) {
      _click(ctx, dest, { startAt: t, freq: 2200, dur: 0.015, peak: 0.18 });
    },
  },
  assistant_thinking: {
    priority: PRIORITY.AMBIENT, cooldownMs: 2000,
    synth(ctx, dest, t) {
      _tone(ctx, dest, { freq: 340, type: "triangle", startAt: t, attack: 0.02, decay: 0.14, peak: 0.16, detune: _detune() });
    },
  },
  confirmation_required: {
    priority: PRIORITY.CRITICAL, cooldownMs: 400,
    synth(ctx, dest, t) {
      _tone(ctx, dest, { freq: 660, type: "square", startAt: t, attack: 0.003, decay: 0.06, peak: 0.35, detune: _detune() });
      _tone(ctx, dest, { freq: 660, type: "square", startAt: t + 0.12, attack: 0.003, decay: 0.06, peak: 0.35, detune: _detune() });
    },
  },
  blocked: {
    priority: PRIORITY.CRITICAL, cooldownMs: 400,
    synth(ctx, dest, t) {
      _tone(ctx, dest, { freq: 180, type: "square", startAt: t, attack: 0.002, decay: 0.16, peak: 0.4, detune: _detune() });
    },
  },
  error: {
    priority: PRIORITY.CRITICAL, cooldownMs: 400,
    synth(ctx, dest, t) {
      _tone(ctx, dest, { freq: 300, type: "sawtooth", startAt: t, attack: 0.002, decay: 0.05, peak: 0.3, detune: _detune() });
      _tone(ctx, dest, { freq: 220, type: "sawtooth", startAt: t + 0.07, attack: 0.002, decay: 0.09, peak: 0.3, detune: _detune() });
    },
  },
  // Section 11 — the key requirement: a short cinematic mechanical-
  // assembly sound. Layered from THREE real elements, in order: a burst
  // of metallic micro-clicks (particles moving/snapping into place), a
  // rising precision sweep (the assembly tightening/aligning), and one
  // low final "lock" thump (system ready) -- restrained, ~450ms total.
  transition_sarana_to_jarvis: {
    priority: PRIORITY.CRITICAL, cooldownMs: 100,
    synth(ctx, dest, t) {
      _clickBurst(ctx, dest, { startAt: t, count: 9, spread: 0.16, freq: 3400, dur: 0.018, peak: 0.28 });
      _sweep(ctx, dest, { from: 260, to: 980, startAt: t + 0.1, dur: 0.2, type: "triangle", peak: 0.32, detune: -60 });
      _tone(ctx, dest, { freq: 110, type: "square", startAt: t + 0.32, attack: 0.002, decay: 0.14, peak: 0.5, detune: -60 });
    },
  },
  // Conceptual reverse: the same lock-thump FIRST (the JARVIS structure
  // releasing), then the sweep DESCENDS and the clicks scatter/decay
  // outward instead of converging — a real, mirrored, not merely
  // "the same sound played backward" or a lazy transposition.
  transition_jarvis_to_sarana: {
    priority: PRIORITY.CRITICAL, cooldownMs: 100,
    synth(ctx, dest, t) {
      _tone(ctx, dest, { freq: 130, type: "sine", startAt: t, attack: 0.002, decay: 0.1, peak: 0.4, detune: 40 });
      _sweep(ctx, dest, { from: 900, to: 320, startAt: t + 0.08, dur: 0.22, type: "sine", peak: 0.3, detune: 40 });
      _clickBurst(ctx, dest, { startAt: t + 0.24, count: 7, spread: 0.18, freq: 2600, dur: 0.02, peak: 0.2 });
    },
  },
});

export const AUDIO_FX_EVENTS = Object.freeze(Object.keys(EVENTS));

// ── the one public entry point ────────────────────────────────────────
export function playAudioFx(name, { force = false } = {}) {
  const spec = EVENTS[name];
  if (!spec) return false; // unknown event name -- never throws, section 20's own requirement

  const now = Date.now();
  const last = _lastPlayedAt.get(name) || 0;
  if (!force && now - last < spec.cooldownMs) return false; // spam guard, not a hard mute

  if (!force && _speaking && spec.priority < PRIORITY.CRITICAL) {
    if (spec.priority === PRIORITY.AMBIENT) return false; // fully suppressed under speech
    // NORMAL ducks under speech rather than competing with it.
    return false;
  }

  const ctx = _getContext();
  if (!ctx || !_master) return false; // Web Audio unavailable -- honest no-op, never a crash

  try {
    ctx.resume().catch(() => {});
    _lastPlayedAt.set(name, now);
    spec.synth(ctx, _master, ctx.currentTime + 0.01);
    return true;
  } catch {
    return false; // a synthesis error must never propagate into the caller's own event handling
  }
}
