"""
tests/test_deployment_providers.py — actions/deployment_providers.py
(J11) in isolation: provider selection, credential loading, and the
one real HTTP call path (`RenderProvider._request()`), with the actual
`requests` call mocked (no real Render API calls in the automated
suite — this project has no configured Render API key at all, and even
if it did, mutating a real production service on every test run would
be exactly what section 25's "do not modify production merely to
satisfy a test requirement" forbids). The real, credential-free health
check (which needs no mocking at all) is covered in
test_deployment_control.py; the one real E2E involving an actual
provider call — none, since no credentials exist — is documented as a
disclosed limitation in the J11 completion report, not faked here.

Run with:
    .venv/Scripts/python.exe -m tests.test_deployment_providers
"""
from unittest.mock import MagicMock, patch

import actions.deployment_providers as dp


def _provider(api_key: str = "fake-key-XYZ789", service_id: str = "srv-fake123") -> dp.RenderProvider:
    with patch.dict("os.environ", {"RENDER_API_KEY": api_key, "RENDER_SERVICE_ID": service_id}):
        return dp.RenderProvider()


# ── Provider selection ───────────────────────────────────────────────────

def test_get_provider_render_returns_a_render_provider() -> None:
    provider, err = dp.get_provider("render")
    assert err is None
    assert isinstance(provider, dp.RenderProvider)
    print("test_get_provider_render_returns_a_render_provider: PASS")


def test_get_provider_default_is_render() -> None:
    provider, err = dp.get_provider()
    assert err is None
    assert provider.name == "render"
    print("test_get_provider_default_is_render: PASS")


def test_get_provider_unknown_name_is_an_honest_error_not_a_silent_render_default() -> None:
    provider, err = dp.get_provider("vercel")
    assert provider is None
    assert "unknown" in err.lower() and "vercel" in err.lower()
    print("test_get_provider_unknown_name_is_an_honest_error_not_a_silent_render_default: PASS")


# ── Credential loading ───────────────────────────────────────────────────

def test_missing_credentials_raise_before_any_http_call() -> None:
    # Explicitly cleared rather than relying on this being the real,
    # currently-unconfigured environment -- must hold even once real
    # Render credentials are eventually configured for actual use.
    import os
    env = dict(os.environ)
    env.pop("RENDER_API_KEY", None)
    env.pop("RENDER_SERVICE_ID", None)
    with patch.dict("os.environ", env, clear=True), \
         patch("actions.deployment_providers._load_config_value", return_value=""):
        provider = dp.RenderProvider()
        with patch("actions.deployment_providers.requests.request") as m_req:
            try:
                provider.get_service()
                raised = False
            except dp.ProviderCredentialsMissing:
                raised = True
    assert raised
    m_req.assert_not_called()
    print("test_missing_credentials_raise_before_any_http_call: PASS")


def test_env_vars_take_priority_over_config_file() -> None:
    with patch.dict("os.environ", {"RENDER_API_KEY": "env-key", "RENDER_SERVICE_ID": "env-service"}), \
         patch("actions.deployment_providers._load_config_value", return_value="should-not-be-used"):
        provider = dp.RenderProvider()
    assert provider.api_key == "env-key"
    assert provider.service_id == "env-service"
    print("test_env_vars_take_priority_over_config_file: PASS")


# ── HTTP request construction / error mapping ────────────────────────────

def test_request_builds_the_correct_bearer_header_and_url() -> None:
    provider = _provider(api_key="secret-token-ABC123")
    fake_resp = MagicMock(status_code=200, content=b'{"id":"srv-fake123","name":"sarana"}')
    fake_resp.json.return_value = {"id": "srv-fake123", "name": "sarana"}
    with patch("actions.deployment_providers.requests.request", return_value=fake_resp) as m_req:
        result = provider.get_service()
    assert result == {"id": "srv-fake123", "name": "sarana"}
    args, kwargs = m_req.call_args
    assert args[0] == "GET"
    assert args[1] == "https://api.render.com/v1/services/srv-fake123"
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token-ABC123"
    print("test_request_builds_the_correct_bearer_header_and_url: PASS")


def test_401_is_mapped_to_provider_credentials_missing_not_a_generic_error() -> None:
    provider = _provider()
    fake_resp = MagicMock(status_code=401, text="Unauthorized")
    with patch("actions.deployment_providers.requests.request", return_value=fake_resp):
        try:
            provider.get_service()
            raised = None
        except dp.ProviderError as e:
            raised = e
    assert isinstance(raised, dp.ProviderCredentialsMissing)
    print("test_401_is_mapped_to_provider_credentials_missing_not_a_generic_error: PASS")


def test_5xx_is_a_provider_error_never_a_false_success() -> None:
    provider = _provider()
    fake_resp = MagicMock(status_code=503, text="Service Unavailable")
    with patch("actions.deployment_providers.requests.request", return_value=fake_resp):
        try:
            provider.get_service()
            raised = None
        except dp.ProviderError as e:
            raised = e
    assert raised is not None
    assert not isinstance(raised, dp.ProviderCredentialsMissing)
    print("test_5xx_is_a_provider_error_never_a_false_success: PASS")


def test_the_api_key_never_appears_in_a_raised_error_message() -> None:
    secret = "sk-render-super-secret-VALUE-999"
    provider = _provider(api_key=secret)
    fake_resp = MagicMock(status_code=500, text=f"Internal error, auth header was Bearer {secret}")
    with patch("actions.deployment_providers.requests.request", return_value=fake_resp):
        try:
            provider.get_service()
        except dp.ProviderError as e:
            message = str(e)
    assert secret not in message
    print("test_the_api_key_never_appears_in_a_raised_error_message: PASS")


def test_network_exception_is_a_provider_error_with_no_leaked_detail() -> None:
    import requests as _requests
    provider = _provider()
    with patch("actions.deployment_providers.requests.request", side_effect=_requests.exceptions.ConnectionError("boom")):
        try:
            provider.get_service()
            raised = None
        except dp.ProviderError as e:
            raised = e
    assert raised is not None
    print("test_network_exception_is_a_provider_error_with_no_leaked_detail: PASS")


# ── Endpoint shape (verified against Render's own real API docs) ────────

def test_list_deploys_uses_the_correct_path_and_bounds_the_limit() -> None:
    provider = _provider()
    fake_resp = MagicMock(status_code=200, content=b"[]")
    fake_resp.json.return_value = []
    with patch("actions.deployment_providers.requests.request", return_value=fake_resp) as m_req:
        provider.list_deploys(limit=999)
    args, kwargs = m_req.call_args
    assert args[0] == "GET"
    assert args[1] == "https://api.render.com/v1/services/srv-fake123/deploys?limit=20"
    print("test_list_deploys_uses_the_correct_path_and_bounds_the_limit: PASS")


def test_trigger_deploy_posts_the_optional_commit_id_only_when_given() -> None:
    provider = _provider()
    fake_resp = MagicMock(status_code=201, content=b'{"id":"dep-1"}')
    fake_resp.json.return_value = {"id": "dep-1"}
    with patch("actions.deployment_providers.requests.request", return_value=fake_resp) as m_req:
        provider.trigger_deploy("")
    args, kwargs = m_req.call_args
    assert args[0] == "POST" and args[1] == "https://api.render.com/v1/services/srv-fake123/deploys"
    assert kwargs["json"] is None

    with patch("actions.deployment_providers.requests.request", return_value=fake_resp) as m_req2:
        provider.trigger_deploy("abc1234")
    _, kwargs2 = m_req2.call_args
    assert kwargs2["json"] == {"commitId": "abc1234"}
    print("test_trigger_deploy_posts_the_optional_commit_id_only_when_given: PASS")


def test_restart_and_rollback_use_the_correct_paths() -> None:
    provider = _provider()
    fake_resp = MagicMock(status_code=200, content=b"{}")
    fake_resp.json.return_value = {}
    with patch("actions.deployment_providers.requests.request", return_value=fake_resp) as m_req:
        provider.restart()
    args, _ = m_req.call_args
    assert args[0] == "POST" and args[1] == "https://api.render.com/v1/services/srv-fake123/restart"

    fake_resp2 = MagicMock(status_code=201, content=b'{"id":"dep-2"}')
    fake_resp2.json.return_value = {"id": "dep-2"}
    with patch("actions.deployment_providers.requests.request", return_value=fake_resp2) as m_req2:
        provider.rollback("dep-1")
    args2, kwargs2 = m_req2.call_args
    assert args2[0] == "POST" and args2[1] == "https://api.render.com/v1/services/srv-fake123/rollback"
    assert kwargs2["json"] == {"deployId": "dep-1"}
    print("test_restart_and_rollback_use_the_correct_paths: PASS")


def _run() -> None:
    test_get_provider_render_returns_a_render_provider()
    test_get_provider_default_is_render()
    test_get_provider_unknown_name_is_an_honest_error_not_a_silent_render_default()
    test_missing_credentials_raise_before_any_http_call()
    test_env_vars_take_priority_over_config_file()
    test_request_builds_the_correct_bearer_header_and_url()
    test_401_is_mapped_to_provider_credentials_missing_not_a_generic_error()
    test_5xx_is_a_provider_error_never_a_false_success()
    test_the_api_key_never_appears_in_a_raised_error_message()
    test_network_exception_is_a_provider_error_with_no_leaked_detail()
    test_list_deploys_uses_the_correct_path_and_bounds_the_limit()
    test_trigger_deploy_posts_the_optional_commit_id_only_when_given()
    test_restart_and_rollback_use_the_correct_paths()
    print("\nAll deployment_providers tests passed.")


if __name__ == "__main__":
    _run()
