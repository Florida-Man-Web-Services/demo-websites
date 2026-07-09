# Dev shell for the MCP server: python + a bootstrapped .venv with the dev
# deps (pytest, httpx). Usage:
#
#   cd mcp-server
#   nix-shell                    # or, from anywhere: nix develop .#mcp-server
#   python -m pytest tests/ -v
#   MCP_AUTH_TOKEN=devtoken python server.py    # http://localhost:8036/mcp
{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages = with pkgs; [ python3 ];

  shellHook = ''
    dir="$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")/mcp-server"
    [ -d "$dir" ] || dir="$PWD"
    if [ ! -d "$dir/.venv" ]; then
      echo "creating mcp-server/.venv and installing python deps (first run only)..."
      python -m venv "$dir/.venv"
      "$dir/.venv/bin/pip" install -q -r "$dir/requirements-dev.txt"
    fi
    export PATH="$dir/.venv/bin:$PATH"
    echo "mcp-server shell ready (python from .venv)"
  '';
}
