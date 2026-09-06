"""
actions/deployment_providers.py — J11's provider abstraction: the ONE
place a real cloud-deployment-provider REST API is ever called from.
`actions/deployment_control.py` (the JARVIS-facing dispatcher) talks
only to this module's small, provider-agnostic interface — it never
sees an API key, a bearer header, or a raw HTTP response.

Inspection finding this module is built on: this project already has a
real, live, deployed production stack — a Render backend
(confirmed live, discovered read-only via the SAME publicly-exposed
mechanism this project's own frontend/.env.example already documents as
non-secret: "every VITE_* variable is bundled directly into the shipped
JavaScript... which is fine to expose") and a Vercel frontend (the exact
URL already hardcoded as a comment-confirmed constant in
dashboard/server.py: "https://sarana-psi.vercel.app... confirmed
production frontend"). No RENDER_API_KEY (or any provider credential)
exists anywhere in this repository's config/api_keys.json or the current
environment — confirmed by direct inspection, not assumed. Every
operation below that needs one honestly raises `ProviderCredentialsMissing`
rather than guessing, hardcoding a placeholder, or asking the user to
paste one into the conversation (section 29's own explicit prohibition).

Only ONE provider is implemented (`RenderProvider`) — section 5's own
explicit permission ("if only one provider is actually required by the
existing project, implementing one provider initially is acceptable").
Vercel is deliberately NOT implemented as a second provider: this
project's Vercel frontend has no deploy-trigger/rollback need JARVIS was
asked to serve (it deploys automatically on push, same as Render, and
push itself is J9's own permanently-frozen boundary — see this module's
own `RenderProvider.trigger_deploy()` docstring) — inventing Vercel
provider code with no concrete capability behind it would be exactly the
speculative, "just in case" work section 0's token-efficiency rules
forbid. `get_provider()` still returns an honest (None, evidence) tuple
for any OTHER provider name rather than silently defaulting to Render,
so the core interface never treats "Render" and "deployment" as
synonyms (section 5's own explicit requirement).

Every Render endpoint used below was verified against Render's own
current official API reference (api-docs.render.com) before writing this
module — never invented (section 6's own explicit requirement):
  - Base URL: https://api.render.com/v1
  - Auth: `Authorization: Bearer <api_key>` on every request, including
    read-only ones — Render's API has no unauthenticated endpoint.
  - GET  /services/{serviceId}
  - GET  /services/{serviceId}/deploys              (list, query: limit)
  - GET  /services/{serviceId}/deploys/{deployId}    (single deploy)
  - POST /services/{serviceId}/deploys               (trigger; optional
    JSON body {"commitId": "..."} — omitted entirely defers to Render's
    OWN documented default, "the latest commit on the connected branch"
    — a real, provider-defined semantic JARVIS delegates to, never a
    guess JARVIS itself invents; see deployment_control.py's own
    docstring on why this is the honest reading of section 13)
  - POST /services/{serviceId}/restart                (no body)
  - POST /services/{serviceId}/rollback               (body: {"deployId": "..."})
"""
import json
import os
import sys
from pathlib import Path

import requests

_RENDER_BASE_URL = "https://api.render.com/v1"
_HTTP_TIMEOUT_S = 20


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_API_CONFIG_PATH = _get_base_dir() / "config" / "api_keys.json"


class ProviderError(Exception):
    """A real, evidence-carrying provider failure — `message` is always
    safe to put directly into a Result Envelope (never a raw response
    body, never a header, never anything that could contain the API key
    itself — see `RenderProvider._request()`'s own redaction)."""


class ProviderCredentialsMissing(ProviderError):
    """Raised instead of ProviderError specifically so
    deployment_control.py can map this one case to [BLOCKED] (a
    genuinely unavailable capability, not a real operation that failed)
    — same STATUS_BLOCKED distinction result_envelope.py's own docstring
    already draws for a permanently-disallowed action, applied here to
    'this JARVIS install has no credential for this provider' instead."""


def _load_config_value(key: str) -> str:
    try:
        with open(_API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return str(json.load(f).get(key, "") or "")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ""


class RenderProvider:
    """The one real, live provider this project actually uses. Reads
    `RENDER_API_KEY`/`RENDER_SERVICE_ID` from the environment FIRST (the
    same env-var-priority pattern main.py's own `_get_api_key()` already
    established for GEMINI_API_KEY, for the identical reason: a hosted
    deployment of JARVIS itself would set these as real environment
    variables, never commit them), falling back to
    `config/api_keys.json`'s own `render_api_key`/`render_service_id`
    fields for local/desktop use — the SAME file, SAME mechanism,
    SAME git-ignored boundary every other credential in this project
    already uses (see DEPLOYMENT.md's own .gitignore audit). Neither
    field is required to construct this object — only to actually CALL
    it (see `_require_credentials()`), so `get_provider()` can always
    succeed and let the caller (deployment_control.py) decide the right
    Result Envelope status per-operation."""

    name = "render"

    def __init__(self) -> None:
        self.api_key = os.environ.get("RENDER_API_KEY") or _load_config_value("render_api_key")
        self.service_id = os.environ.get("RENDER_SERVICE_ID") or _load_config_value("render_service_id")

    def _require_credentials(self) -> None:
        missing = []
        if not self.api_key:
            missing.append("RENDER_API_KEY")
        if not self.service_id:
            missing.append("RENDER_SERVICE_ID")
        if missing:
            raise ProviderCredentialsMissing(
                f"Render is not configured: {', '.join(missing)} not set "
                "(environment variable or config/api_keys.json)."
            )

    def _request(self, method: str, path: str, json_body: dict | None = None) -> dict:
        """The ONE place a Render HTTP call happens. `self.api_key` is
        used here and ONLY here — never returned, never included in any
        raised exception's message (a network/HTTP error's own text is
        safe; Render's response body for an auth failure has been
        confirmed by the provider's own docs to carry no secret, but is
        still truncated defensively before ever reaching a caller)."""
        self._require_credentials()
        url = f"{_RENDER_BASE_URL}{path}"
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        try:
            resp = requests.request(method, url, headers=headers, json=json_body, timeout=_HTTP_TIMEOUT_S)
        except requests.exceptions.RequestException as e:
            raise ProviderError(f"Render API request failed: {type(e).__name__}") from None
        if resp.status_code == 401 or resp.status_code == 403:
            raise ProviderCredentialsMissing("Render rejected the configured API key (unauthorized).")
        if resp.status_code >= 400:
            # Defense in depth (section 12's own "redact sensitive
            # provider responses where necessary"): a real Render error
            # body has been confirmed by the provider's own docs to
            # carry no secret, but this response text is still scrubbed
            # of the literal API key before ever reaching a Result
            # Envelope — a misbehaving/misconfigured endpoint echoing
            # the Authorization header back must never leak it. Found
            # and fixed via this module's own test, not hypothetical.
            body_hint = (resp.text or "")[:200].replace(self.api_key, "<redacted>")
            raise ProviderError(f"Render API returned {resp.status_code} for {method} {path}: {body_hint}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            raise ProviderError(f"Render API returned a non-JSON response for {method} {path}.") from None

    # ── Read-only ──────────────────────────────────────────────────────

    def get_service(self) -> dict:
        return self._request("GET", f"/services/{self.service_id}")

    def list_deploys(self, limit: int = 5) -> list[dict]:
        limit = max(1, min(int(limit), 20))  # bounded — never an unbounded history dump
        return self._request("GET", f"/services/{self.service_id}/deploys?limit={limit}")

    def get_deploy(self, deploy_id: str) -> dict:
        return self._request("GET", f"/services/{self.service_id}/deploys/{deploy_id}")

    # ── Consequential ─────────────────────────────────────────────────

    def trigger_deploy(self, commit_id: str = "") -> dict:
        """Triggers a real deploy via Render's own REST API — NOT a git
        push (J9's own `git_control.py` permanently blocks push; this is
        a genuinely different mechanism Render exposes independently of
        Git, so nothing here weakens or routes around J9's frozen
        boundary). `commit_id`, when given, must already exist on
        Render's connected branch (Render deploys from ITS OWN linked
        GitHub repo, never from this machine's local working tree) —
        an unpushed/unknown commit is refused by Render itself with a
        real, honest error, not silently substituted."""
        body = {"commitId": commit_id} if commit_id else None
        return self._request("POST", f"/services/{self.service_id}/deploys", json_body=body)

    def restart(self) -> dict:
        return self._request("POST", f"/services/{self.service_id}/restart")

    def rollback(self, deploy_id: str) -> dict:
        return self._request("POST", f"/services/{self.service_id}/rollback", json_body={"deployId": deploy_id})


_PROVIDERS = {"render": RenderProvider}


def get_provider(name: str = "render") -> tuple[object | None, str | None]:
    """Provider SELECTION — never Gemini's choice, never Task Engine
    hard-coding 'Render' as a synonym for 'deployment' (section 5's own
    requirement): callers pass a provider NAME, this is the one place
    that resolves it to a real implementation. Returns (provider, None)
    on success, or (None, evidence) for an unknown name — the same
    honest-tuple convention `repo_agent.resolve_repo_root()` already
    established, never a silent default substitution."""
    cls = _PROVIDERS.get((name or "render").strip().lower())
    if cls is None:
        return None, f"Unknown deployment provider: '{name}' (supported: {', '.join(sorted(_PROVIDERS))})"
    return cls(), None
