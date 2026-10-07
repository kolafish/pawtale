import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import wechat_miniprogram_code


@pytest.fixture
def wechat(monkeypatch):
    requests = []
    responses = {}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responses[request.url.path]

    real_client = httpx.AsyncClient
    monkeypatch.setattr(wechat_miniprogram_code.httpx, "AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(wechat_miniprogram_code, "get_settings", lambda: SimpleNamespace(
        wechat_miniprogram_appid="wx-app", wechat_miniprogram_secret="secret",
        wechat_miniprogram_code_env_version="trial",
    ))
    monkeypatch.setattr(wechat_miniprogram_code, "_cached_token", None)
    responses["/cgi-bin/stable_token"] = httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
    return requests, responses


async def test_code_opens_the_page_with_scene_and_reuses_the_token(wechat):
    requests, responses = wechat
    responses["/wxa/getwxacodeunlimit"] = httpx.Response(200, content=b"png", headers={"content-type": "image/png"})

    for _ in range(2):
        assert await wechat_miniprogram_code.fetch_miniprogram_code(scene="a" * 20, page="pages/p/index") == b"png"

    assert [request.url.path for request in requests].count("/cgi-bin/stable_token") == 1
    code_request = requests[-1]
    assert code_request.url.params["access_token"] == "token-1"
    body = json.loads(code_request.content)
    assert body["scene"] == "a" * 20 and body["page"] == "pages/p/index"
    # The page ships with a later release, and testers scan trial builds.
    assert body["check_path"] is False and body["env_version"] == "trial"


async def test_wechat_error_payload_is_reported(wechat):
    _requests, responses = wechat
    responses["/wxa/getwxacodeunlimit"] = httpx.Response(200, json={"errcode": 41030, "errmsg": "invalid page"})

    with pytest.raises(wechat_miniprogram_code.WeChatMiniProgramCodeError, match="41030"):
        await wechat_miniprogram_code.fetch_miniprogram_code(scene="b" * 20, page="pages/p/index")
