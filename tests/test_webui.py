import json
import urllib.error
import urllib.request

import pytest

import webui


@pytest.fixture
def server():
    received = []
    s = webui.Server(received.append)
    s.received = received
    yield s
    s.httpd.shutdown()


def request(server, path, token=None, host=None, data=None):
    req = urllib.request.Request(f"http://127.0.0.1:{server.port}{path}", data=data)
    if token:
        req.add_header("X-Murmur-Token", token)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def test_state_requires_token(server):
    server.publish({"hello": 1})
    assert request(server, "/api/state")[0] == 403
    assert request(server, "/api/state", token="wrong")[0] == 403
    status, body = request(server, "/api/state", token=server.token)
    assert status == 200 and json.loads(body)["state"] == {"hello": 1}


def test_foreign_host_refused(server):  # DNS-rebinding protection
    assert request(server, "/api/state", token=server.token, host="evil.example:80")[0] == 403
    assert request(server, "/", host="evil.example:80")[0] == 403


def test_actions_need_token(server):
    body = json.dumps({"type": "quit"}).encode()
    assert request(server, "/api/action", data=body)[0] == 403
    assert server.received == []
    assert request(server, "/api/action", token=server.token, data=body)[0] == 200
    assert server.received == [{"type": "quit"}]


def test_serves_interface_but_nothing_else(server):
    assert request(server, "/")[0] == 200
    assert request(server, "/../settings.json")[0] in (403, 404)
    assert request(server, "/app.py")[0] == 404
