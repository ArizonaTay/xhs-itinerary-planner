"""Sign-in relay: parsing the container's replies and surfacing them."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import login, mcp, notes
from app.models import XhsLoginQr


def _result(*texts, images=None, is_error=False):
    content = [SimpleNamespace(text=text) for text in texts]
    for data, mime in images or []:
        content.append(SimpleNamespace(data=data, mimeType=mime))
    return SimpleNamespace(isError=is_error, content=content)


def test_auth_headers_follow_the_token(monkeypatch):
    monkeypatch.setattr(
        mcp, "settings", SimpleNamespace(xhs=SimpleNamespace(auth_token=""))
    )
    assert mcp.auth_headers() == {}

    monkeypatch.setattr(
        mcp, "settings", SimpleNamespace(xhs=SimpleNamespace(auth_token="secret"))
    )
    assert mcp.auth_headers() == {"Authorization": "Bearer secret"}


@pytest.mark.parametrize(
    ("texts", "logged_in", "username"),
    [
        (['{"is_login": true, "username": "旅行日记"}'], True, "旅行日记"),
        (['{"is_login": false}'], False, None),
        (["已登录，用户名: 旅行日记"], True, "旅行日记"),
        (["未登录，请先扫码"], False, None),
        (["Login required"], False, None),
        (["logged in as bob"], True, None),
        (["something unparseable"], False, None),
    ],
)
def test_parse_status(texts, logged_in, username):
    assert login.parse_status(texts) == (logged_in, username)


def test_images_become_data_urls():
    found = mcp.images(
        _result(
            "scan me",
            images=[
                ("aGVsbG8=", "image/png"),
                ("data:image/jpeg;base64,world", "image/jpeg"),
                ("not-an-image", "text/plain"),
            ],
        )
    )
    assert found == [
        "data:image/png;base64,aGVsbG8=",
        "data:image/jpeg;base64,world",
    ]


def test_root_cause_unwraps_exception_groups():
    inner = ConnectionRefusedError("All connection attempts failed")
    group = ExceptionGroup("unhandled errors in a TaskGroup", [inner])

    cause = mcp.root_cause(group)

    assert "ConnectionRefusedError" in cause
    assert "All connection attempts failed" in cause


@pytest.mark.asyncio
async def test_status_endpoint(client, monkeypatch):
    async def fake_status():
        return True, "旅行日记", "已登录，用户名: 旅行日记"

    monkeypatch.setattr(login, "status", fake_status)

    response = await client.get("/api/xhs/login")

    assert response.status_code == 200
    data = response.json()
    assert data["logged_in"] is True
    assert data["username"] == "旅行日记"
    assert data["mcp_url"] == mcp.settings.xhs.mcp_url


@pytest.mark.asyncio
async def test_status_when_mcp_unreachable(client, monkeypatch):
    async def fake_status():
        raise HTTPException(status_code=502, detail="Cannot reach the MCP server")

    monkeypatch.setattr(login, "status", fake_status)

    response = await client.get("/api/xhs/login")
    assert response.status_code == 502


@pytest.mark.asyncio
async def test_qrcode_endpoint(client, monkeypatch):
    async def fake_qrcode():
        return XhsLoginQr(image="data:image/png;base64,aGVsbG8=", hint="二维码 3 分钟内有效")

    monkeypatch.setattr(login, "qrcode", fake_qrcode)

    response = await client.post("/api/xhs/login/qrcode")

    assert response.status_code == 200
    data = response.json()
    assert data["image"] == "data:image/png;base64,aGVsbG8="
    assert "二维码" in data["hint"]


@pytest.mark.asyncio
async def test_qrcode_without_image_is_502(monkeypatch):
    async def fake_call(tool, arguments=None):
        return _result("qr generation failed")

    monkeypatch.setattr(mcp, "call_tool", fake_call)

    with pytest.raises(HTTPException) as excinfo:
        await login.qrcode()

    assert excinfo.value.status_code == 502
    assert excinfo.value.detail["code"] == "XHS_QRCODE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_logout_endpoint(client, monkeypatch):
    async def fake_logout():
        return {"ok": True, "message": "已删除 cookies"}

    monkeypatch.setattr(login, "logout", fake_logout)

    response = await client.post("/api/xhs/logout")

    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.asyncio
async def test_search_maps_not_logged_in_to_401(monkeypatch):
    async def fake_call(tool, arguments=None):
        return _result("未登录，请先登录", is_error=True)

    monkeypatch.setattr(mcp, "call_tool", fake_call)

    with pytest.raises(HTTPException) as excinfo:
        await notes.search_notes("Snow Town")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail["code"] == "XHS_NOT_LOGGED_IN"


@pytest.mark.asyncio
async def test_search_maps_other_errors_to_502(monkeypatch):
    async def fake_call(tool, arguments=None):
        return _result("服务器内部错误", is_error=True)

    monkeypatch.setattr(mcp, "call_tool", fake_call)

    with pytest.raises(HTTPException) as excinfo:
        await notes.search_notes("Snow Town")

    assert excinfo.value.status_code == 502
