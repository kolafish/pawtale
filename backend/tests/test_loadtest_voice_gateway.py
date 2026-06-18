from argparse import Namespace

import httpx

from app.auth import SESSION_COOKIE_NAME
from scripts.loadtest_voice_gateway import (
    GatewayResult,
    TestUserSpec as LoadtestUserSpec,
    build_user_specs,
    gateway_ws_url,
    loadtest_passed,
    normalize_base_url,
    prepare_voice_sessions,
    session_cookie_header,
)


def test_normalize_base_url_requires_absolute_http_url():
    assert normalize_base_url(" https://pawtale-api.spaceflows.cn/ ") == "https://pawtale-api.spaceflows.cn"

    try:
        normalize_base_url("pawtale-api.spaceflows.cn")
    except ValueError as exc:
        assert "absolute http(s)" in str(exc)
    else:
        raise AssertionError("expected invalid URL to fail")


def test_gateway_ws_url_preserves_base_path_and_switches_scheme():
    assert (
        gateway_ws_url("https://example.test/prefix/", "session-1")
        == "wss://example.test/prefix/api/mobile/memoir/voice-sessions/session-1/gateway"
    )
    assert (
        gateway_ws_url("http://127.0.0.1:8101", "session-2")
        == "ws://127.0.0.1:8101/api/mobile/memoir/voice-sessions/session-2/gateway"
    )


def test_build_user_specs_uses_explicit_prefix_domain_and_password():
    specs = build_user_specs(
        2,
        Namespace(
            email_prefix=" LoadTest ",
            email_domain="Example.TEST",
            password="secret-password",
        ),
    )

    assert [spec.email for spec in specs] == ["loadtest-0@example.test", "loadtest-1@example.test"]
    assert {spec.password for spec in specs} == {"secret-password"}
    assert [spec.display_name for spec in specs] == ["压测用户 0", "压测用户 1"]


def test_session_cookie_header_extracts_pawtale_cookie_only():
    cookies = httpx.Cookies()
    cookies.set("other", "ignored")
    cookies.set(SESSION_COOKIE_NAME, "token-value")

    assert session_cookie_header(cookies) == f"{SESSION_COOKIE_NAME}=token-value"


def test_loadtest_passed_requires_ready_ok_results_and_expected_rejection():
    args = Namespace(users=2, expect_eleventh_rejected=True)
    ready_results = [
        GatewayResult(index=0, email="u0@example.test", session_id="s0", status="ready"),
        GatewayResult(index=1, email="u1@example.test", session_id="s1", status="ready"),
    ]
    final_results = [
        GatewayResult(index=0, email="u0@example.test", session_id="s0", status="ok"),
        GatewayResult(index=1, email="u1@example.test", session_id="s1", status="ok"),
    ]
    eleventh = GatewayResult(index=2, email="u2@example.test", session_id="s2", status="rejected")

    assert loadtest_passed(final_results, ready_results, eleventh, args)


def test_loadtest_failed_when_eleventh_is_not_rejected():
    args = Namespace(users=1, expect_eleventh_rejected=True)
    ready_results = [GatewayResult(index=0, email="u0@example.test", session_id="s0", status="ready")]
    final_results = [GatewayResult(index=0, email="u0@example.test", session_id="s0", status="ok")]
    eleventh = GatewayResult(index=1, email="u1@example.test", session_id="s1", status="unexpected")

    assert not loadtest_passed(final_results, ready_results, eleventh, args)


async def test_prepare_voice_sessions_sends_explicit_cookie_header_for_secure_cookie():
    client = FakeLoadtestClient()
    specs = [
        LoadtestUserSpec(
            index=0,
            email="loadtest-0@example.test",
            password="secret-password",
            display_name="压测用户 0",
        )
    ]

    sessions = await prepare_voice_sessions(client, specs)

    assert sessions[0].id == "voice-session-1"
    assert client.voice_session_headers == [{"Cookie": f"{SESSION_COOKIE_NAME}=secure-token"}]


class FakeLoadtestResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected status {self.status_code}")


class FakeLoadtestClient:
    def __init__(self):
        self.cookies = httpx.Cookies()
        self.voice_session_headers = []

    async def post(self, url, *, json, headers=None):
        if url == "/api/auth/register":
            self.cookies.set(SESSION_COOKIE_NAME, "secure-token")
            return FakeLoadtestResponse(
                201,
                {
                    "user": {
                        "id": "user-1",
                    }
                },
            )
        if url == "/api/mobile/memoir/voice-sessions":
            self.voice_session_headers.append(headers)
            return FakeLoadtestResponse(201, {"session": {"id": "voice-session-1"}})
        raise AssertionError(f"unexpected URL {url}")
