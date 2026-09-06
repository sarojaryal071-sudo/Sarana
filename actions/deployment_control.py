"""
actions/deployment_control.py — JARVIS's J11 deployment/production
capability: a small, EXPLICIT allowlist of operations
(health_check/status/history/deploy/restart/rollback) over
`deployment_providers.py`'s own provider abstraction, with every
consequential action gated through the EXISTING
`result_envelope.is_consequential()`/`is_confirmed()` mechanism and
every outcome independently re-verified against real production state —
never a raw provider response mistaken for the truth (section 8/9's own
explicit requirement).

Reused, never duplicated:
  - `actions/deployment_providers.py`'s `get_provider()`/`RenderProvider`
    — the ONE place a real Render HTTP call happens; this module never
    touches `requests`/an API key/a bearer header directly for provider
    calls (it DOES make its own plain, unauthenticated HTTP GETs for the
    health check below — see that function's own docstring on why that
    is a genuinely different, credential-free operation).
  - `actions/result_envelope.py` — the ONE shared status vocabulary and
    the ONE centralized `is_consequential()`/`is_confirmed()` gate (new
    `"provider_deploy"`/`"provider_restart"`/`"provider_rollback"`
    entries, same tier as `delete`/`repo_edit`/`git_commit`) — never a
    second confirmation framework, never a new status vocabulary.

Deliberately NOT built, and why:
  - `deployment_control(command="...")` / any arbitrary provider-request
    passthrough — section 21's own explicit, hard prohibition. Every
    action below is a fixed Python function; nothing here ever lets a
    caller supply a raw HTTP method/path/body of their own choosing.
  - Arbitrary shell, cloud CLI, SQL, or infrastructure commands — no
    safe existing mechanism for any of these exists in this repository
    (the same finding J7's own "Terminal" half reached), and section 11
    explicitly forbids inventing one here.
  - An automatic "deployment failed -> retry/restart/rollback" loop —
    section 15's own explicit prohibition; a failed deploy is reported
    honestly and left there. The only "recovery" this module performs
    is BOUNDED POLLING of a single already-in-flight operation's own
    status (verification, not a new mutation) — see `_poll_deploy()`.
  - A second Vercel provider — see `deployment_providers.py`'s own
    docstring for why only Render is implemented.
  - An automatic rollback-on-failed-deploy — section 31's own explicit
    requirement that rollback stay a distinct, explicitly-requested,
    confirmation-gated action, never an automatic recovery step.
"""
import os
import time

import requests

from actions import result_envelope as _envelope
from actions.deployment_providers import get_provider, ProviderError, ProviderCredentialsMissing

# The two REAL, currently-live production URLs for this project — see
# this module's own inspection finding (module docstring is in
# deployment_providers.py; both URLs were confirmed live via a real,
# read-only HTTP GET during this stage's own implementation, not
# assumed). Neither is a secret (the backend URL is bundled directly
# into the frontend's own shipped JavaScript — frontend/.env.example's
# own comment: "which is fine to expose"). Overridable via environment
# variables so tests (and a future redeploy to a different URL) never
# need to edit this file — same SARANA_* env-var convention
# dashboard/server.py's own CORS origin handling already uses.
_DEFAULT_BACKEND_HEALTH_URL = "https://sarana-m9g6.onrender.com/api/session"
_DEFAULT_FRONTEND_HEALTH_URL = "https://sarana-psi.vercel.app/"
_HEALTH_HTTP_TIMEOUT_S = 15

# Bounded polling (section 16's own explicit requirement) — never
# unbounded, never a busy-loop: a fixed interval, a fixed maximum
# duration, always terminates one way or another.
_POLL_INTERVAL_S = 5
_DEPLOY_POLL_TIMEOUT_S = 180
_RESTART_HEALTH_TIMEOUT_S = 90

# Render's own real, documented deploy status vocabulary (verified
# against api-docs.render.com before writing this module — never
# invented). Anything not in either set is still in progress.
_DEPLOY_SUCCESS_STATUSES = frozenset({"live"})
_DEPLOY_FAILURE_STATUSES = frozenset({"build_failed", "update_failed", "canceled", "pre_deploy_failed"})


def _backend_health_url() -> str:
    return os.environ.get("SARANA_BACKEND_HEALTH_URL", _DEFAULT_BACKEND_HEALTH_URL)


def _frontend_health_url() -> str:
    return os.environ.get("SARANA_FRONTEND_HEALTH_URL", _DEFAULT_FRONTEND_HEALTH_URL)


def _check_one_url(url: str) -> tuple[bool, str]:
    try:
        resp = requests.get(url, timeout=_HEALTH_HTTP_TIMEOUT_S)
        return (200 <= resp.status_code < 300), f"HTTP {resp.status_code}"
    except requests.exceptions.RequestException as e:
        return False, f"{type(e).__name__}"


def health_check() -> str:
    """A real, credential-free production health check — a plain HTTPS
    GET against the actual live backend and frontend, exactly the
    verification step this project's own DEPLOYMENT.md already
    documents doing manually (`curl https://.../api/session`). No
    RenderProvider/API key involved at all: this checks the SERVICE
    itself, not the provider's own opinion of it (section 9's own
    'a health check should verify the actual service, not merely the
    deployment provider' requirement)."""
    backend_ok, backend_evidence = _check_one_url(_backend_health_url())
    frontend_ok, frontend_evidence = _check_one_url(_frontend_health_url())
    evidence = f"backend {_backend_health_url()} -> {backend_evidence}; frontend {_frontend_health_url()} -> {frontend_evidence}"
    if backend_ok and frontend_ok:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, evidence)
    if not backend_ok and not frontend_ok:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, evidence)
    # One up, one down is a real, partial, still-actionable finding —
    # not a clean pass, but not a total outage either.
    return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, evidence)


def _map_provider_error(e: Exception) -> str:
    if isinstance(e, ProviderCredentialsMissing):
        return _envelope.envelope(_envelope.STATUS_BLOCKED, str(e))
    if isinstance(e, ProviderError):
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, str(e))
    return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Unexpected provider error: {type(e).__name__}: {e}")


def deployment_status(provider_name: str = "render") -> str:
    """Read-only — service metadata plus the single most recent deploy's
    own status/commit, giving an honest 'what is currently live' answer
    without needing a separate history call."""
    provider, err = get_provider(provider_name)
    if provider is None:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, err)
    try:
        service = provider.get_service()
        recent = provider.list_deploys(limit=1)
    except (ProviderError, Exception) as e:
        return _map_provider_error(e)
    latest = recent[0] if recent else {}
    commit = (latest.get("commit") or {}).get("id", "")
    evidence = (
        f"service '{service.get('name', provider_name)}' ({service.get('suspended', 'unknown')}); "
        f"latest deploy: {latest.get('status', 'unknown')}"
        + (f" (commit {commit[:8]})" if commit else "")
    )
    return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, evidence)


def deployment_history(provider_name: str = "render", limit: int = 5) -> str:
    """Read-only — the provider's own real, recent deploy history."""
    provider, err = get_provider(provider_name)
    if provider is None:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, err)
    try:
        deploys = provider.list_deploys(limit=limit)
    except Exception as e:
        return _map_provider_error(e)
    if not deploys:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, "no deploys found")
    lines = []
    for d in deploys:
        commit = (d.get("commit") or {}).get("id", "")
        lines.append(f"{d.get('id', '?')}: {d.get('status', 'unknown')}" + (f" (commit {commit[:8]})" if commit else "") + f" [{d.get('trigger', '?')}]")
    return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"{len(deploys)} recent deploy(s):\n" + "\n".join(lines))


def _poll_deploy(provider, deploy_id: str, timeout_s: int | None = None) -> tuple[dict | None, str | None]:
    """Bounded polling of ONE already-triggered deploy's own status —
    verification of an in-flight operation, never a new mutation (see
    module docstring). `timeout_s` is resolved from the module-level
    `_DEPLOY_POLL_TIMEOUT_S` INSIDE the function body, deliberately not
    as a bound default parameter — Python binds a default value once,
    at function-definition time, so a test patching the module constant
    would otherwise have no effect (found live while writing this
    stage's own timeout test, not hypothetical). Returns
    (final_deploy_dict, None) once a
    TERMINAL status is reached, or (last_seen_deploy_dict_or_None,
    'timeout') if the bound is hit while still non-terminal — the
    caller decides INCONCLUSIVE vs failure from there, this function
    never guesses."""
    if timeout_s is None:
        timeout_s = _DEPLOY_POLL_TIMEOUT_S
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        try:
            last = provider.get_deploy(deploy_id)
        except Exception:
            time.sleep(_POLL_INTERVAL_S)
            continue
        status = (last or {}).get("status", "")
        if status in _DEPLOY_SUCCESS_STATUSES or status in _DEPLOY_FAILURE_STATUSES:
            return last, None
        time.sleep(_POLL_INTERVAL_S)
    return last, "timeout"


def deploy(provider_name: str = "render", commit_id: str = "", confirmed: bool = False) -> str:
    """Consequential — gated through the EXISTING is_consequential()/
    is_confirmed() classifier. `commit_id`, when given, must already be
    pushed to Render's own connected branch (see
    RenderProvider.trigger_deploy()'s own docstring); when omitted,
    Render's own documented default ('the latest commit on the
    connected branch') is used — a real provider semantic JARVIS
    delegates to, never a guess JARVIS invents itself (section 13's own
    'do not invent a latest commit' is about JARVIS fabricating a
    commit hash, not about deferring to the provider's own, real,
    already-pushed state).

    Verification (section 8's own required lifecycle): the triggered
    deploy is polled (bounded) until Render reports a TERMINAL status;
    only a `live` status proceeds to a REAL production health check
    (never provider-reported health alone) — [VERIFIED_SUCCESS] requires
    BOTH. A requested commit that doesn't match what Render actually
    deployed is flagged honestly rather than silently accepted."""
    provider, err = get_provider(provider_name)
    if provider is None:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, err)

    if _envelope.is_consequential(action_name="provider_deploy") and not _envelope.is_confirmed({"confirmed": confirmed}):
        target = f"commit {commit_id[:8]}" if commit_id else "the latest commit on Render's connected branch"
        return _envelope.envelope(_envelope.STATUS_CONFIRMATION_REQUIRED, f"this will deploy {target} to production ({provider_name})")

    try:
        triggered = provider.trigger_deploy(commit_id)
    except Exception as e:
        return _map_provider_error(e)

    deploy_id = triggered.get("id", "")
    if not deploy_id:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "deploy was requested but Render returned no deploy id to verify against")

    final, timed_out = _poll_deploy(provider, deploy_id)
    if final is None:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"deploy {deploy_id} was triggered but its status could not be retrieved")
    status = final.get("status", "")
    if timed_out:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"deploy {deploy_id} had not reached a final state after {_DEPLOY_POLL_TIMEOUT_S}s (last status: {status})")
    if status in _DEPLOY_FAILURE_STATUSES:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"deploy {deploy_id} ended with status '{status}'")

    deployed_commit = (final.get("commit") or {}).get("id", "")
    if commit_id and deployed_commit and not deployed_commit.startswith(commit_id) and not commit_id.startswith(deployed_commit):
        return _envelope.envelope(
            _envelope.STATUS_VERIFIED_FAILURE,
            f"deploy {deploy_id} reports status '{status}' but deployed commit {deployed_commit[:8]} does not match the requested commit {commit_id[:8]}",
        )

    health = health_check()
    health_ok = health.startswith(f"[{_envelope.STATUS_VERIFIED_SUCCESS}]")
    evidence = f"deploy {deploy_id} status '{status}'" + (f" (commit {deployed_commit[:8]})" if deployed_commit else "") + f"; production health: {_envelope.STATUS_VERIFIED_SUCCESS if health_ok else 'not confirmed'}"
    if health_ok:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, evidence)
    return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, evidence)


def restart(provider_name: str = "render", confirmed: bool = False) -> str:
    """Consequential — same confirmation gate as deploy. Render's own
    restart endpoint returns success once the restart is ACCEPTED, not
    once the service is actually back up — this function still polls
    the REAL health endpoint afterward (bounded) before ever claiming
    VERIFIED_SUCCESS, since a brief outage during restart is expected
    and 'the API call succeeded' is exactly the false-success shape
    section 8 warns against."""
    provider, err = get_provider(provider_name)
    if provider is None:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, err)

    if _envelope.is_consequential(action_name="provider_restart") and not _envelope.is_confirmed({"confirmed": confirmed}):
        return _envelope.envelope(_envelope.STATUS_CONFIRMATION_REQUIRED, f"this will restart the production service ({provider_name})")

    try:
        provider.restart()
    except Exception as e:
        return _map_provider_error(e)

    deadline = time.monotonic() + _RESTART_HEALTH_TIMEOUT_S
    last_health = ""
    while time.monotonic() < deadline:
        last_health = health_check()
        if last_health.startswith(f"[{_envelope.STATUS_VERIFIED_SUCCESS}]"):
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"restart accepted; production health confirmed afterward ({last_health})")
        time.sleep(_POLL_INTERVAL_S)
    return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"restart accepted, but production health was not confirmed within {_RESTART_HEALTH_TIMEOUT_S}s (last check: {last_health})")


def rollback(provider_name: str = "render", deploy_id: str = "", confirmed: bool = False) -> str:
    """Consequential — the rollback TARGET must be an explicit,
    unambiguous deploy id (section 31's own requirement: never a guessed
    'previous' deploy). Same poll + real health verification as deploy()."""
    if not (deploy_id or "").strip():
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "rollback needs an explicit target deploy id — ask the user which deploy to roll back to, or check the deployment history first")

    provider, err = get_provider(provider_name)
    if provider is None:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, err)

    if _envelope.is_consequential(action_name="provider_rollback") and not _envelope.is_confirmed({"confirmed": confirmed}):
        return _envelope.envelope(_envelope.STATUS_CONFIRMATION_REQUIRED, f"this will roll back production ({provider_name}) to deploy {deploy_id}")

    try:
        triggered = provider.rollback(deploy_id)
    except Exception as e:
        return _map_provider_error(e)

    new_deploy_id = triggered.get("id", "")
    if not new_deploy_id:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"rollback to {deploy_id} was requested but Render returned no deploy id to verify against")

    final, timed_out = _poll_deploy(provider, new_deploy_id)
    if final is None:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"rollback deploy {new_deploy_id} was triggered but its status could not be retrieved")
    status = final.get("status", "")
    if timed_out:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"rollback deploy {new_deploy_id} had not reached a final state after {_DEPLOY_POLL_TIMEOUT_S}s (last status: {status})")
    if status in _DEPLOY_FAILURE_STATUSES:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"rollback to {deploy_id} ended with status '{status}'")

    health = health_check()
    health_ok = health.startswith(f"[{_envelope.STATUS_VERIFIED_SUCCESS}]")
    evidence = f"rolled back to {deploy_id} (new deploy {new_deploy_id}, status '{status}'); production health: {_envelope.STATUS_VERIFIED_SUCCESS if health_ok else 'not confirmed'}"
    if health_ok:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, evidence)
    return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, evidence)


def deployment_control(parameters: dict = None) -> str:
    """The one entry point main.py/task_engine.py call — mirrors the
    established xxx_control(parameters={...}) convention every other
    JARVIS capability module already uses. Deliberately NO 'command'
    parameter exists anywhere in this module (section 21's own hard
    requirement) — only these fixed action names are ever recognized."""
    params = parameters or {}
    action = (params.get("action") or "").lower().strip()
    provider_name = params.get("provider", "render")

    if action == "health_check":
        return health_check()
    if action == "status":
        return deployment_status(provider_name)
    if action == "history":
        return deployment_history(provider_name, int(params.get("limit", 5)))
    if action == "deploy":
        return deploy(provider_name, params.get("commit_id", ""), bool(params.get("confirmed", False)))
    if action == "restart":
        return restart(provider_name, bool(params.get("confirmed", False)))
    if action == "rollback":
        return rollback(provider_name, params.get("deploy_id", ""), bool(params.get("confirmed", False)))

    return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Unknown or unsupported deployment action: '{action}'")
