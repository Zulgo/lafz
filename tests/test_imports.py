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
