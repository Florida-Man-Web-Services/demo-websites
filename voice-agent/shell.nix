# Everything the voice agent needs on NixOS: python, ffmpeg (audio decode for
# playback), libopus (Discord's voice codec). On first entry it creates .venv
# and installs the python deps, so the full setup is just:
#
#   cd voice-agent
#   nix-shell                    # or, from anywhere in the repo: nix develop
#   python discord_bot.py        # or: python chat.py ole-barn --voice
#                                # or: uvicorn server:app --port 8035
{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages = with pkgs; [ python3 ffmpeg libopus ];

  # discord.py can't find_library("opus") on NixOS; discord_bot.py reads this.
  DISCORD_OPUS_LIB = "${pkgs.libopus}/lib/libopus.so.0";

  shellHook = ''
    # Anchor on the repo root so `nix develop` from anywhere (and `nix-shell`
    # from this directory) both land the venv at voice-agent/.venv.
    dir="$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")/voice-agent"
    [ -d "$dir" ] || dir="$PWD"
    if [ ! -d "$dir/.venv" ]; then
      echo "creating voice-agent/.venv and installing python deps (first run only)..."
      python -m venv "$dir/.venv"
      "$dir/.venv/bin/pip" install -q -r "$dir/requirements.txt" -r "$dir/requirements-discord.txt"
    fi
    export PATH="$dir/.venv/bin:$PATH"
    echo "voice-agent shell ready (python from .venv, ffmpeg + opus from nix)"
  '';
}
