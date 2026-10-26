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
