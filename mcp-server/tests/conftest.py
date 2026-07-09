"""Make mcp-server/ and voice-agent/ importable from tests."""
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = MCP_DIR.parent

# voice-agent/ has its own server.py; insert it first so MCP_DIR ends up
# at sys.path[0] and `import server` in tests resolves to mcp-server/server.py.
sys.path.insert(0, str(REPO_ROOT / "voice-agent"))
sys.path.insert(0, str(MCP_DIR))
