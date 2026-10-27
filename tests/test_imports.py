import importlib
import sys

import pytest


@pytest.mark.parametrize("module", ["engine", "learning", "system", "webui", "app"])
def test_module_imports(module):
    importlib.import_module(module)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only modules")
@pytest.mark.parametrize("module", ["pill", "menubar", "macwindow"])
def test_mac_modules_import(module):
    importlib.import_module(module)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_private_files_are_owner_only():
    import os
    import numpy as np
    import system
    system.save_json("history.json", [{"text": "secret"}])
    system.save_recording(np.zeros(10, dtype="float32"), {"time": 1.0, "text": "secret"})
    folder = system.config_dir()
    assert os.stat(folder).st_mode & 0o077 == 0
    for root, _, files in os.walk(folder):
        for f in files:
            assert os.stat(os.path.join(root, f)).st_mode & 0o077 == 0, f


def test_murmur_data_moves_to_lafz():
    import os
    import system
    old = os.path.join(system._settings_base(), "Murmur")
    os.makedirs(old)
    with open(os.path.join(old, "history.json"), "w") as f:
        f.write("[]")
    system.load_json("instance.json", None)  # the very first lookup, as when checking for a running copy
    assert not os.path.exists(old)
    assert os.path.exists(os.path.join(system.config_dir(), "history.json"))


def test_old_murmur_is_asked_to_quit():
    import json, threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import app, system
    got = []

    class OldMurmur(BaseHTTPRequestHandler):  # answers only to Murmur's own header, like Murmur 0.5.0
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            ok = self.headers.get("X-Murmur-Token") == "secret"
            if ok:
                got.append(body["type"])
            self.send_response(200 if ok else 403)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = HTTPServer(("127.0.0.1", 0), OldMurmur)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    system.save_json("instance.json", {"port": server.server_address[1], "token": "secret"})
    try:
        assert app.already_running() is False  # Lafz starts...
        assert got == ["quit"]                 # ...after asking the old Murmur to quit
    finally:
        server.shutdown()


def test_murmur_folder_used_until_it_can_move(monkeypatch):
    import os
    import system
    old = os.path.join(system._settings_base(), "Murmur")
    os.makedirs(old)
    rename = os.rename
    def locked(*a):
        raise PermissionError("in use")
    monkeypatch.setattr(system.os, "rename", locked)
    assert system.config_dir() == old  # Windows refused the move: keep using the user's data this time
    monkeypatch.setattr(system.os, "rename", rename)
    assert system.config_dir().endswith("Lafz") and not os.path.exists(old)  # moved on the next launch
