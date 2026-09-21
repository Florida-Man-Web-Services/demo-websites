import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp-server"))

import pytest  # noqa: E402

# Modules that carry process-global mode/state derived from env vars. Tests
# that importlib.reload() them under mutated env (AGENT_MODE, paths, ...)
# otherwise leak that state into later tests (e.g. site-content assertions
# seeing an AI411 prompt after an auto-mode routing test ran).
_MODE_MODULES = (
    "config",
    "agent",
    "onboarding",
    "owner_updates",
    "ai411",
    "unified",
    "mcp_bridge",
    "site_content",
    "callback_dial",
)


@pytest.fixture(autouse=True)
def _restore_mode_modules():
    """Reload mode-carrying modules after each test, under restored env.

    Runs last (autouse fixtures set up first are torn down last, i.e. after
    monkeypatch has undone the test's env changes), so reloads capture the
    baseline environment.
    """
    yield
    import importlib

    for name in _MODE_MODULES:
        mod = sys.modules.get(name)
        if mod is None:
            continue
        try:
            importlib.reload(mod)
        except Exception:  # noqa: BLE001 - never mask the real test result
            sys.modules.pop(name, None)
