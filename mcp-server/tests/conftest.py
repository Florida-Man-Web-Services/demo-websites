"""Make mcp-server/ and voice-agent/ importable from tests."""
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = MCP_DIR.parent

sys.path.insert(0, str(MCP_DIR))
sys.path.insert(0, str(REPO_ROOT / "voice-agent"))
