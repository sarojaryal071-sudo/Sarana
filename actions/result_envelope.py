"""
result_envelope.py — the ONE canonical result shape a computer-control
operation returns before Gemini ever sees it, plus the ONE centralized
risk/confirmation classifier. Shared by actions/computer_control.py
(accomplish() and its Tier 3-6 machinery) and actions/computer_settings.py
(Tier 1/2 native actions), so:

  - every verifiable action reports one of the same five statuses, instead
    of each action inventing its own ad-hoc "Done."/"Clicked" string
  - confirmation logic for consequential actions lives in ONE place,
    never reimplemented per action (see is_consequential/is_confirmed)

This module is intentionally small and pure — no I/O, no state, nothing
persisted. See the JARVIS architecture design (Universal Computer Control
Architecture) for the full rationale; this is its concrete implementation.
"""

STATUS_VERIFIED_SUCCESS      = "VERIFIED_SUCCESS"
STATUS_VERIFIED_FAILURE      = "VERIFIED_FAILURE"
STATUS_INCONCLUSIVE          = "INCONCLUSIVE"
STATUS_UI_AMBIGUOUS          = "UI_AMBIGUOUS"
STATUS_CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
STATUS_CANCELLED             = "CANCELLED"
# BLOCKED is deliberately distinct from CONFIRMATION_REQUIRED: a
# CONFIRMATION_REQUIRED action becomes allowed the moment the user says
# yes (confirmed=true); a BLOCKED action is refused by policy regardless
# of confirmation — there is no confirmed=true that makes it proceed.
# Used for things like a generated-code safety-check rejection
# (actions/desktop.py) or a permanently-disallowed operation (force-push,
# arbitrary shell execution) — never for something a user can authorize.
STATUS_BLOCKED                = "BLOCKED"

# Statuses that mean "local verification could not tell" — the ONLY ones
# worth escalating to the existing observe/verify vision mechanism (once,
# same session, same cooldown guard — see main.py's computer_control
# branch). VERIFIED_FAILURE and CONFIRMATION_REQUIRED are NOT escalated:
# a failure is already a known, real outcome (escalating wouldn't change
# it), and a confirmation gate must never be bypassed by "let's just look
# and decide" — it requires an actual user yes, not a vision guess.
ESCALATABLE_STATUSES = frozenset({STATUS_INCONCLUSIVE, STATUS_UI_AMBIGUOUS})

_DIRECTIVES = {
    STATUS_VERIFIED_SUCCESS: "",
    STATUS_VERIFIED_FAILURE: (
        "Tell the user honestly that this did not work."
    ),
    STATUS_INCONCLUSIVE: (
        "Do not tell the user this succeeded. Call action='verify' for a "
        "direct look, try a different approach, or ask the user — never "
        "assume it worked."
    ),
    STATUS_UI_AMBIGUOUS: (
        "Do not guess which one is meant. Call accomplish again with a "
        "narrower target/constraints/control_type, or ask the user which "
        "one they mean."
    ),
    STATUS_CONFIRMATION_REQUIRED: (
        "Do NOT perform this action yet. Ask the user to explicitly "
        "confirm this specific action, then call this again with "
        "confirmed=true — never infer confirmation from the original "
        "request or from unrelated speech."
    ),
    STATUS_CANCELLED: (
        "This was cancelled before completion — do not describe it as "
        "having succeeded or failed; tell the user honestly that it was "
        "stopped."
    ),
    STATUS_BLOCKED: (
        "This action is blocked by policy and cannot be performed — this "
        "is not a confirmation gate, so do not ask the user to confirm "
        "it or suggest a workaround that bypasses the restriction. Tell "
        "the user honestly why it can't be done."
    ),
}


def envelope(status: str, evidence: str) -> str:
    """Builds the one canonical tagged result string. `status` must be
    one of the STATUS_* constants above. `evidence` is a short, honest
    description of what was actually observed — never what was assumed
    or hoped for. Format: "[STATUS] evidence. directive" — the SAME
    bracketed-directive convention already used throughout this codebase
    ([JARVIS_MODE_REQUIRED], [UI_AMBIGUOUS], [VISION_ACTIVE]), extended
    to a single unified, general-purpose set."""
    tag = f"[{status}]"
    ev = (evidence or "").strip()
    if ev and not ev.endswith((".", "!", "?")):
        ev += "."
    directive = _DIRECTIVES.get(status, "")
    parts = [tag, ev]
    if directive:
        parts.append(directive)
    return " ".join(p for p in parts if p)


# ── Centralized risk classifier ───────────────────────────────────────────
#
# ONE place that decides whether an action needs explicit confirmation
# before it runs. Deliberately NOT a second LLM call (see the architecture
# doc's "one reasoning session" rule) — a small, explicit, auditable list,
# matched against the SEMANTIC SHAPE of the action (send/delete/purchase/
# security/disconnect-while-active), never against an application name.
# "sleep" is deliberately NOT here — it's disruptive-sounding but fully
# reversible (nothing is lost), unlike shutdown/restart which close every
# running app; the architecture doc's own examples list shutdown/restart,
# not sleep, as consequential.

_CONSEQUENTIAL_ACTION_NAMES = frozenset({
    "shutdown", "restart",
})

_CONSEQUENTIAL_GOAL_PATTERNS = (
    "send", "delete", "remove", "purchase", "buy", "pay", "payment",
    "transfer money", "wire", "checkout", "submit", "unsubscribe",
    "delete account", "close account", "password", "security setting",
    "disconnect", "uninstall", "format", "factory reset",
    "sign out everywhere", "log out everywhere",
)


def is_consequential(action_name: str = "", goal: str = "") -> bool:
    """True if this action/goal belongs in the 'ask first' risk tier.
    Low-risk actions (open, navigate, click, type ordinary text, read,
    search, connect an ordinary device) are NOT flagged here and must
    never require confirmation — see the architecture doc's safety
    model. Checked against BOTH the literal Tier-1/2 action name (for
    computer_settings.py's enum-style dispatch) and a free-text goal
    (for accomplish()'s goal-oriented calls) — either can trigger it."""
    if (action_name or "").strip().lower() in _CONSEQUENTIAL_ACTION_NAMES:
        return True
    text = (goal or "").strip().lower()
    return any(p in text for p in _CONSEQUENTIAL_GOAL_PATTERNS)


def is_confirmed(params: dict) -> bool:
    """Accepts both a real boolean confirmed=true (accomplish()'s
    convention) and the pre-existing truthy-string convention
    computer_settings.py's shutdown/restart already used
    (confirmed="yes"/"true"/"1"/"confirm") so both tools share one
    reading of the same field without changing computer_settings.py's
    already-shipped, already-tested behavior."""
    val = (params or {}).get("confirmed", False)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("yes", "true", "1", "confirm")
