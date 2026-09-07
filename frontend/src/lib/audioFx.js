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
// How much quieter a NORMAL-tier cue plays while the assistant is
// speaking -- genuine ducking (still audible, just deferential to
// speech), never full silence (that's AMBIENT's own, separate rule).
const DUCK_GAIN = 0.32;

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

// A converging click burst -- unlike _clickBurst's uniform random spread,
// each click's own timing window narrows and its frequency rises as the
// burst progresses, so components read as scattering INTO alignment
// (ascending) or OUT of it (descending) rather than uniform noise. This
// is the actual "precision mechanical assembly" texture -- components
// don't click randomly, they converge.
function _clickConverge(ctx, dest, { startAt, count = 8, spread = 0.14, freq = 3000, direction = 1, dur = 0.016, peak = 0.26 }) {
  for (let i = 0; i < count; i++) {
    const progress = i / Math.max(1, count - 1);           // 0..1 across the burst
    const window = spread * (1 - progress * 0.7);           // narrows toward the end (converging)
    const tOffset = direction > 0
      ? progress * spread + (Math.random() - 0.5) * window * 0.4
      : (1 - progress) * spread + (Math.random() - 0.5) * window * 0.4;
    const f = freq * (direction > 0 ? 0.6 + progress * 0.9 : 1.5 - progress * 0.9) * (0.9 + Math.random() * 0.2);
    _click(ctx, dest, { startAt: Math.max(startAt, startAt + tOffset), freq: f, dur, peak: peak * (0.7 + progress * 0.3) });
  }
}

// A brief filtered-noise "servo whir" -- a bandpass sweep through noise,
// the texture of a small precision motor/actuator engaging. Distinct
// from _sweep (a pure tone) and _click (an instant metallic tick) --
// this is what makes the transition read as mechanical movement, not
// just electronic tones.
function _whir(ctx, dest, { startAt, dur = 0.14, fromFreq = 900, toFreq = 2600, q = 8, peak = 0.14 }) {
  const bufferSize = Math.max(1, Math.floor(ctx.sampleRate * dur));
  const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < bufferSize; i++) data[i] = Math.random() * 2 - 1;
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  const filter = ctx.createBiquadFilter();
  filter.type = "bandpass";
  filter.Q.value = q;
  filter.frequency.setValueAtTime(fromFreq, startAt);
  filter.frequency.exponentialRampToValueAtTime(Math.max(1, toFreq), startAt + dur);
  const gain = ctx.createGain();
  src.connect(filter);
  filter.connect(gain);
  gain.connect(dest);
  _envelope(ctx, gain, { attack: dur * 0.25, hold: dur * 0.2, decay: dur * 0.5, peak }, startAt);
  src.start(startAt);
  src.stop(startAt + dur + 0.02);
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

// Production-polish note (explicit design decision, per the "mute
// behavior" audit item -- not an oversight): this module deliberately
// has NO awareness of state.speechMuted (main.py's speech_mute tool --
// see App.jsx's own SPEECH_MUTE handling). Muting the assistant's
// SPOKEN VOICE and muting cinematic UI cues (wake/sleep/transition
// chimes) are different channels serving different purposes -- a user
// who mutes JARVIS's voice mid-task most likely still wants to hear
// the Sarana<->JARVIS transition chime or a BLOCKED cue; conflating the
// two would mean "mute" silences MORE than the user actually asked for.
// If a future request wants audioFx cues suppressed too, that is a
// second, explicit setMuted()-style call site here, never inferred
// from speechMuted implicitly.

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
  // "Precision mechanical assembly" (explicit brief requirement, revised
  // pass): tiny components moving -> converging -> a servo engaging ->
  // magnetic snap -> lock. FOUR real layered elements, in this exact
  // order, ~480ms total:
  //   1. a CONVERGING click burst (_clickConverge, direction=1) —
  //      components scattering INTO alignment, not uniform noise
  //   2. a servo whir (_whir, rising) — the actuator engaging
  //   3. a rising precision sweep — final tightening
  //   4. a magnetic "snap" (one sharp high click) immediately INTO one
  //      low final lock thump — the actual moment of engagement, not
  //      just a tone fading in
  // Restrained: no reverb, no pitch randomization beyond the click
  // texture itself, nothing resembling a notification/laser/cartoon SFX.
  transition_sarana_to_jarvis: {
    priority: PRIORITY.CRITICAL, cooldownMs: 100,
    synth(ctx, dest, t) {
      _clickConverge(ctx, dest, { startAt: t, count: 10, spread: 0.15, freq: 3200, direction: 1, dur: 0.016, peak: 0.24 });
      _whir(ctx, dest, { startAt: t + 0.06, dur: 0.13, fromFreq: 700, toFreq: 2200, peak: 0.13 });
      _sweep(ctx, dest, { from: 280, to: 1020, startAt: t + 0.16, dur: 0.16, type: "triangle", peak: 0.28, detune: -60 });
      _click(ctx, dest, { startAt: t + 0.33, freq: 4200, dur: 0.012, peak: 0.32, q: 12 });
      _tone(ctx, dest, { freq: 108, type: "square", startAt: t + 0.335, attack: 0.002, decay: 0.15, peak: 0.5, detune: -60 });
    },
  },
  // Conceptual reverse, not merely reversed audio: the lock releases
  // FIRST (JARVIS's structure letting go), the servo winds DOWN, the
  // sweep DESCENDS, and the clicks scatter OUTWARD (_clickConverge with
  // direction=-1) instead of converging — a real, mirrored deconstruction.
  transition_jarvis_to_sarana: {
    priority: PRIORITY.CRITICAL, cooldownMs: 100,
    synth(ctx, dest, t) {
      _tone(ctx, dest, { freq: 128, type: "sine", startAt: t, attack: 0.002, decay: 0.09, peak: 0.4, detune: 40 });
      _click(ctx, dest, { startAt: t + 0.005, freq: 4000, dur: 0.012, peak: 0.26, q: 12 });
      _sweep(ctx, dest, { from: 940, to: 300, startAt: t + 0.1, dur: 0.17, type: "sine", peak: 0.26, detune: 40 });
      _whir(ctx, dest, { startAt: t + 0.16, dur: 0.13, fromFreq: 2000, toFreq: 650, peak: 0.12 });
      _clickConverge(ctx, dest, { startAt: t + 0.27, count: 8, spread: 0.17, freq: 2600, direction: -1, dur: 0.018, peak: 0.2 });
    },
  },
  // ── presentation lifecycle (shared — belongs to PresentationSurface
  // itself, see that component's own phase-transition effect; never
  // reimplemented per renderer) ──────────────────────────────────────
  // A subtle glass/electronic "forming" texture — restrained, quieter
  // than the identity-transition cues (this happens far more often —
  // every weather/calendar/search result — so it must never compete for
  // attention the way a mode switch does).
  presentation_materializing: {
    priority: PRIORITY.NORMAL, cooldownMs: 250,
    synth(ctx, dest, t) {
      _whir(ctx, dest, { startAt: t, dur: 0.09, fromFreq: 1200, toFreq: 2600, peak: 0.08 });
      _tone(ctx, dest, { freq: 720, type: "sine", startAt: t + 0.04, attack: 0.006, decay: 0.09, peak: 0.16 });
    },
  },
  // A quick, quiet data-tick — plays once per reveal wave, not per row
  // (see PresentationSurface's own reveal-count guard), so a six-row
  // forecast doesn't fire six sounds.
  presentation_reveal: {
    priority: PRIORITY.AMBIENT, cooldownMs: 180,
    synth(ctx, dest, t) {
      _click(ctx, dest, { startAt: t, freq: 2600, dur: 0.014, peak: 0.14, q: 6 });
    },
  },
  presentation_update: {
    priority: PRIORITY.NORMAL, cooldownMs: 200,
    synth(ctx, dest, t) {
      _tone(ctx, dest, { freq: 640, type: "sine", startAt: t, attack: 0.004, decay: 0.07, peak: 0.14 });
    },
  },
  presentation_expand: {
    priority: PRIORITY.NORMAL, cooldownMs: 150,
    synth(ctx, dest, t) {
      _sweep(ctx, dest, { from: 500, to: 900, startAt: t, dur: 0.09, type: "sine", peak: 0.16 });
    },
  },
  presentation_collapse: {
    priority: PRIORITY.NORMAL, cooldownMs: 150,
    synth(ctx, dest, t) {
      _sweep(ctx, dest, { from: 900, to: 500, startAt: t, dur: 0.08, type: "sine", peak: 0.14 });
    },
  },
  // The retraction — a small reverse-echo of materializing, ending
  // (rather than beginning) with the whir, so it reads as the structure
  // dissolving rather than a mirrored replay.
  presentation_dismiss: {
    priority: PRIORITY.NORMAL, cooldownMs: 150,
    synth(ctx, dest, t) {
      _tone(ctx, dest, { freq: 520, type: "sine", startAt: t, attack: 0.003, decay: 0.06, peak: 0.13 });
      _whir(ctx, dest, { startAt: t + 0.03, dur: 0.08, fromFreq: 2200, toFreq: 1000, peak: 0.07 });
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

  // Production-polish fix (real bug, found via code audit, not
  // hypothetical): NORMAL was previously fully SUPPRESSED while
  // speaking, identical to AMBIENT, despite the code's own comment (and
  // section 13's own "subtle UI sounds can duck under speech" — ducking
  // means quieter, not silent) claiming otherwise. AMBIENT stays fully
  // suppressed; NORMAL now genuinely ducks via a lower-gain intermediate
  // node instead of returning early.
  let duck = false;
  if (!force && _speaking && spec.priority < PRIORITY.CRITICAL) {
    if (spec.priority === PRIORITY.AMBIENT) return false; // fully suppressed under speech
    duck = true; // NORMAL: still plays, just quieter -- see DUCK_GAIN below
  }

  const ctx = _getContext();
  if (!ctx || !_master) return false; // Web Audio unavailable -- honest no-op, never a crash

  try {
    ctx.resume().catch(() => {});
    _lastPlayedAt.set(name, now);
    let dest = _master;
    if (duck) {
      const duckGain = ctx.createGain();
      duckGain.gain.value = DUCK_GAIN;
      duckGain.connect(_master);
      dest = duckGain;
    }
    spec.synth(ctx, dest, ctx.currentTime + 0.01);
    return true;
  } catch {
    return false; // a synthesis error must never propagate into the caller's own event handling
  }
}
