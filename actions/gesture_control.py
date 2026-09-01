"""
actions/gesture_control.py — hand-gesture mouse control (JARVIS Gesture
Mode). Adapted from the technique in sanath-kumar-s/Virtual-Mouse
(MediaPipe Hands landmark tracking + a small gesture-classification
layer), rewritten specifically for this project rather than ported
wholesale — see the module-level notes below on what was kept, changed,
and deliberately left out.

User's own explicit requirements for this integration (not the
reference project's own defaults):
  1. NEVER self-activates or self-deactivates. The reference project
     toggles itself on/off internally via a right-hand-fist gesture; this
     version has NO such internal toggle at all — start()/stop() are the
     ONLY way this ever turns on or off, called exclusively from
     main.py's gesture_mode tool in response to an explicit user request.
  2. NO preview/visualization window of any kind — the reference project
     shows a Qt "hand skeleton" window plus a live stats dashboard; this
     version calls neither cv2.imshow() nor opens any window at all. The
     webcam runs silently in the background; SARANA's existing UI is
     completely unaffected (no new badge, no new panel, no new mode
     indicator — literally nothing visible changes).
  3. Real control of the user's actual OS mouse cursor via pyautogui —
     the SAME library actions/computer_control.py already uses for every
     other mouse action in this project (not a new pynput dependency the
     reference project uses instead), including its FAILSAFE=True escape
     hatch: moving the REAL physical mouse to a screen corner at any time
     immediately raises pyautogui.FailSafeException, which this module
     catches by simply skipping that frame's action — a genuine, always-
     available physical override, not a feature this module could
     accidentally disable.

Deliberately NOT ported from the reference project (disclosed, not
hidden):
  - The Qt GUI dashboard/hand-skeleton preview windows — the user
    explicitly asked for no preview at all.
  - The internal fist-gesture enable/disable toggle — the user
    explicitly asked for explicit-command-only activation.
  - Dual-hand gestures (both-hands-pinch to minimize the active window,
    double-pinch to open a terminal) — real automation actions beyond
    "let my hand be the mouse," and OPEN_TERMINAL in particular is more
    than this first pass should take on unasked. Easy to add later as
    their own gestures if wanted.
  - pynput, screeninfo, pygetwindow-for-this-purpose, wmctrl — this
    project already has pyautogui (mouse+scroll+screen size) and doesn't
    need a second mouse-control library or new dependencies for
    functionality it already has another way.

One deliberate IMPROVEMENT over the reference: pinch detection here uses
a hand-size-NORMALIZED ratio (thumb-to-index distance divided by a
wrist-to-middle-knuckle reference distance) instead of a raw pixel
threshold — the reference's CLICK_THRESHOLD_PX=40 only works correctly
at one specific camera resolution/hand-to-camera distance; a normalized
ratio behaves consistently regardless of how close the user's hand is to
the webcam.
"""
import threading
import time
from pathlib import Path

try:
    import cv2
    _CV2 = True
except ImportError:
    _CV2 = False

try:
    import mediapipe as mp
    from mediapipe.tasks import python as _mp_tasks
    from mediapipe.tasks.python import vision as _mp_vision
    _MEDIAPIPE = True
except ImportError:
    _MEDIAPIPE = False

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import requests
    _REQUESTS = True
except ImportError:
    _REQUESTS = False


def _require(flag: bool, missing: str) -> None:
    if not flag:
        raise RuntimeError(f"{missing} not installed. Run: pip install {missing.lower()}")


# ── hand-landmark model — MediaPipe's modern Tasks API (mp.solutions.hands,
# what the reference project used, was removed in current mediapipe
# releases — see this module's own investigation before writing this)
# needs a separate model-weights file that isn't bundled in the pip
# package. Downloaded once and cached here, never committed to git (see
# .gitignore's own note on this exact path) — the same lazy-download-and-
# cache pattern most ML tooling uses for model weights, rather than
# shipping a ~7.8MB binary in every clone whether or not gesture mode is
# ever used.
_MODEL_DIR = Path(__file__).resolve().parent.parent / "config" / "models"
_MODEL_PATH = _MODEL_DIR / "hand_landmarker.task"
_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)


def _ensure_model() -> str | None:
    """Downloads the hand-landmark model on first use if it isn't already
    cached locally. Returns None on success, or a human-readable error
    string on failure — never raises, so a flaky connection reports
    honestly through start()'s own return value instead of crashing the
    tool call."""
    if _MODEL_PATH.exists() and _MODEL_PATH.stat().st_size > 0:
        return None
    if not _REQUESTS:
        return "The 'requests' package is required to download the hand-tracking model."
    try:
        _MODEL_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = _MODEL_PATH.with_suffix(".task.part")
        with requests.get(_MODEL_URL, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
        # Atomic rename — a download interrupted partway through must
        # never leave a truncated file at the real path where a later
        # run's _MODEL_PATH.exists() check would wrongly trust it.
        tmp_path.replace(_MODEL_PATH)
        return None
    except Exception as e:
        return f"Could not download the hand-tracking model ({e})."


# ── tunables (kept as plain module constants, not a config file — this
# is one focused module, not a multi-file framework) ───────────────────
_CONTROL_BOX_MARGIN = 0.12      # inset fraction of the frame used as the movable area
_PINCH_RATIO_THRESHOLD = 0.35   # thumb-tip↔index-tip distance / wrist↔middle-mcp distance
_DRAG_HOLD_S = 0.35             # how long a pinch must be held before it becomes a drag
_CLICK_COOLDOWN_S = 0.35        # minimum time between two left-clicks
_RIGHT_CLICK_COOLDOWN_S = 0.6   # minimum time between two right-clicks
_SMOOTH_ALPHA = 0.35            # exponential smoothing factor for cursor position (0..1, higher = snappier)
_DEAD_ZONE_PX = 3               # ignore cursor moves smaller than this many screen pixels
_SCROLL_SENSITIVITY = 900       # normalized-y-delta -> scroll "clicks" multiplier

# MediaPipe Hands landmark indices used below (see mediapipe's own hand
# landmark model docs — 21 points per hand, this only needs a few).
_WRIST, _THUMB_TIP, _INDEX_TIP, _INDEX_PIP, _MIDDLE_TIP, _MIDDLE_PIP = 0, 4, 8, 6, 12, 10
_MIDDLE_MCP, _RING_TIP, _RING_PIP, _PINKY_TIP, _PINKY_PIP = 9, 16, 14, 20, 18


class _GestureState:
    """Per-session mutable state for the running loop — a plain object
    (not module globals) so a stop()+start() cycle always begins from a
    clean slate, never carrying over a stale drag/click timestamp from a
    previous session."""

    def __init__(self):
        self.smoothed_x: float | None = None
        self.smoothed_y: float | None = None
        self.last_moved_x: float | None = None
        self.last_moved_y: float | None = None
        self.pinching = False
        self.pinch_started_at = 0.0
        self.dragging = False
        self.last_click_at = 0.0
        self.last_right_click_at = 0.0
        self.right_pinching = False  # left hand's pinch, drives right-click (edge-triggered)
        self.last_scroll_y: float | None = None


_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop_event: threading.Event | None = None
_cap = None  # cv2.VideoCapture, held here so stop() can release it even mid-frame


def is_active() -> bool:
    with _lock:
        return _thread is not None and _thread.is_alive()


def start() -> str:
    """Idempotent — calling start() while already active is a harmless
    no-op that reports the existing state honestly rather than spawning
    a second capture thread."""
    global _thread, _stop_event, _cap
    with _lock:
        if _thread is not None and _thread.is_alive():
            return "Gesture control is already active."
        _require(_CV2, "opencv-python")
        _require(_MEDIAPIPE, "mediapipe")
        _require(_PYAUTOGUI, "pyautogui")

        model_error = _ensure_model()
        if model_error:
            return f"{model_error} Gesture control was NOT activated."

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            cap.release()
            return (
                "Could not open the webcam — it may be in use by another "
                "application, or no camera is available. Gesture control "
                "was NOT activated."
            )
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        _cap = cap
        _stop_event = threading.Event()
        _thread = threading.Thread(
            target=_run_loop, args=(cap, _stop_event), daemon=True, name="gesture-control"
        )
        _thread.start()
        return "Gesture control is now active. No preview window is shown."


def stop() -> str:
    """Idempotent — calling stop() while already inactive is a harmless
    no-op."""
    global _thread, _stop_event, _cap
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = None
            return "Gesture control was already off."
        _stop_event.set()
        thread = _thread
    thread.join(timeout=3.0)  # released outside the lock — join() can briefly block
    with _lock:
        _thread = None
        _stop_event = None
        _cap = None
    return "Gesture control is now off."


def _finger_up(landmarks, tip_idx: int, pip_idx: int) -> bool:
    # Image y increases downward, so an extended (upward-pointing) finger
    # has its tip ABOVE (smaller y than) its own PIP joint. Orientation-
    # independent for the four non-thumb fingers — no handedness needed.
    return landmarks[tip_idx].y < landmarks[pip_idx].y


def _dist(a, b) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def _pinch_ratio(landmarks) -> float:
    hand_scale = _dist(landmarks[_WRIST], landmarks[_MIDDLE_MCP]) or 1e-6
    return _dist(landmarks[_THUMB_TIP], landmarks[_INDEX_TIP]) / hand_scale


def _safe_call(fn, *args, **kwargs) -> None:
    # pyautogui.FAILSAFE=True raises the instant the real cursor sits in
    # a screen corner — the user's own physical mouse always wins over
    # whatever gesture action was about to happen. Skipping the one call
    # (not crashing the loop) is the correct response to that, not an
    # error to surface.
    try:
        fn(*args, **kwargs)
    except pyautogui.FailSafeException:
        pass


def _run_loop(cap, stop_event: threading.Event) -> None:
    # Modern MediaPipe Tasks API (mp.solutions.hands was removed in
    # current mediapipe releases — see this module's own header on that
    # investigation). VIDEO running mode processes one frame at a time
    # against a monotonically-increasing timestamp, using the previous
    # frame's result to track the hand rather than re-detecting from
    # scratch every frame — cheaper and steadier than IMAGE mode.
    landmarker = _mp_vision.HandLandmarker.create_from_options(
        _mp_vision.HandLandmarkerOptions(
            base_options=_mp_tasks.BaseOptions(model_asset_path=str(_MODEL_PATH)),
            num_hands=2,
            running_mode=_mp_vision.RunningMode.VIDEO,
            min_hand_detection_confidence=0.7,
            min_tracking_confidence=0.5,
        )
    )
    screen_w, screen_h = pyautogui.size()
    state = _GestureState()
    m = _CONTROL_BOX_MARGIN
    loop_start = time.monotonic()
    # VIDEO mode requires STRICTLY increasing timestamps between calls on
    # the same landmarker — a real camera's own capture delay normally
    # guarantees that on its own (frames arrive tens of milliseconds
    # apart), but nothing here should silently depend on that. A very
    # fast source (a webcam driver that buffers frames, or simply two
    # loop iterations landing in the same millisecond) can otherwise
    # produce two equal integer-ms timestamps in a row and crash the
    # whole loop — caught by this module's own test suite (a mocked,
    # zero-delay fake camera made it happen immediately) before this
    # ever shipped, not left for it to happen unpredictably later.
    last_timestamp_ms = -1

    try:
        while not stop_event.is_set():
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue

            frame = cv2.flip(frame, 1)  # mirror — moving your hand right moves the cursor right
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            timestamp_ms = max(last_timestamp_ms + 1, int((time.monotonic() - loop_start) * 1000))
            last_timestamp_ms = timestamp_ms
            result = landmarker.detect_for_video(mp_image, timestamp_ms)

            right_hand = None
            left_hand = None
            if result.hand_landmarks and result.handedness:
                for landmarks, handedness in zip(result.hand_landmarks, result.handedness):
                    # The frame was mirrored above, which mirrors chirality —
                    # MediaPipe's label was computed on that already-mirrored
                    # image, so swapping it back here is what actually
                    # matches the user's own real right/left hand.
                    label = handedness[0].category_name
                    real_hand = "Left" if label == "Right" else "Right"
                    if real_hand == "Right" and right_hand is None:
                        right_hand = landmarks
                    elif real_hand == "Left" and left_hand is None:
                        left_hand = landmarks

            now = time.time()

            # ── right hand: cursor movement + primary click/drag ────────
            if right_hand is not None:
                pinch = _pinch_ratio(right_hand) < _PINCH_RATIO_THRESHOLD
                index_up = _finger_up(right_hand, _INDEX_TIP, _INDEX_PIP)
                middle_up = _finger_up(right_hand, _MIDDLE_TIP, _MIDDLE_PIP)
                ring_up = _finger_up(right_hand, _RING_TIP, _RING_PIP)
                pinky_up = _finger_up(right_hand, _PINKY_TIP, _PINKY_PIP)

                # Target point: the pinch midpoint while pinching (feels
                # like "grabbing" that exact point), the index fingertip
                # otherwise — matches the reference's own MOVE gesture.
                if pinch:
                    tx = (right_hand[_THUMB_TIP].x + right_hand[_INDEX_TIP].x) / 2
                    ty = (right_hand[_THUMB_TIP].y + right_hand[_INDEX_TIP].y) / 2
                else:
                    tx, ty = right_hand[_INDEX_TIP].x, right_hand[_INDEX_TIP].y

                # Map the control-box-inset frame to full screen coordinates.
                nx = min(1.0, max(0.0, (tx - m) / (1 - 2 * m)))
                ny = min(1.0, max(0.0, (ty - m) / (1 - 2 * m)))
                raw_x, raw_y = nx * screen_w, ny * screen_h

                if state.smoothed_x is None:
                    state.smoothed_x, state.smoothed_y = raw_x, raw_y
                else:
                    state.smoothed_x += (raw_x - state.smoothed_x) * _SMOOTH_ALPHA
                    state.smoothed_y += (raw_y - state.smoothed_y) * _SMOOTH_ALPHA

                moved = (
                    state.last_moved_x is None
                    or abs(state.smoothed_x - state.last_moved_x) > _DEAD_ZONE_PX
                    or abs(state.smoothed_y - state.last_moved_y) > _DEAD_ZONE_PX
                )

                # Only move when NOT scrolling — index+middle-up drives
                # scroll instead of cursor movement (matches the
                # reference's own SCROLL gesture taking priority).
                scrolling = index_up and middle_up and not ring_up and not pinky_up and not pinch
                if not scrolling and moved:
                    _safe_call(pyautogui.moveTo, state.smoothed_x, state.smoothed_y, _pause=False)
                    state.last_moved_x, state.last_moved_y = state.smoothed_x, state.smoothed_y

                # click / drag state machine
                if pinch and not state.pinching:
                    state.pinching = True
                    state.pinch_started_at = now
                elif pinch and state.pinching:
                    if not state.dragging and (now - state.pinch_started_at) > _DRAG_HOLD_S:
                        state.dragging = True
                        _safe_call(pyautogui.mouseDown, _pause=False)
                elif not pinch and state.pinching:
                    state.pinching = False
                    if state.dragging:
                        state.dragging = False
                        _safe_call(pyautogui.mouseUp, _pause=False)
                    elif (now - state.last_click_at) > _CLICK_COOLDOWN_S:
                        state.last_click_at = now
                        _safe_call(pyautogui.click, _pause=False)

                # scroll — vertical hand movement while the scroll gesture
                # is held; hand moving up scrolls up (standard wheel feel).
                if scrolling:
                    if state.last_scroll_y is not None:
                        delta = state.last_scroll_y - ty  # up = positive
                        if abs(delta) > 0.003:
                            _safe_call(pyautogui.scroll, int(delta * _SCROLL_SENSITIVITY), _pause=False)
                    state.last_scroll_y = ty
                else:
                    state.last_scroll_y = None
            else:
                # No right hand visible — never leave a drag hanging.
                if state.dragging:
                    state.dragging = False
                    _safe_call(pyautogui.mouseUp, _pause=False)
                state.pinching = False
                state.last_scroll_y = None

            # ── left hand: right-click only (secondary control) ─────────
            if left_hand is not None:
                pinch = _pinch_ratio(left_hand) < _PINCH_RATIO_THRESHOLD
                if pinch and not state.right_pinching and (now - state.last_right_click_at) > _RIGHT_CLICK_COOLDOWN_S:
                    state.last_right_click_at = now
                    _safe_call(pyautogui.click, button="right", _pause=False)
                state.right_pinching = pinch
            else:
                state.right_pinching = False

    except Exception as e:
        print(f"[GestureControl] Loop stopped by an unexpected error: {e}")
    finally:
        if state.dragging:
            _safe_call(pyautogui.mouseUp, _pause=False)  # never leave the real mouse mid-drag
        landmarker.close()
        cap.release()
