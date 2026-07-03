# Everything the voice agent needs on NixOS: python, ffmpeg (audio decode for
# playback), libopus (Discord's voice codec). On first entry it creates .venv
# and installs the python deps, so the full setup is just:
#
#   cd voice-agent
#   nix-shell
#   python discord_bot.py        # or: python chat.py ole-barn --voice
#                                # or: uvicorn server:app --port 8035
{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages = with pkgs; [ python3 ffmpeg libopus ];

  # discord.py can't find_library("opus") on NixOS; discord_bot.py reads this.
  DISCORD_OPUS_LIB = "${pkgs.libopus}/lib/libopus.so.0";

  shellHook = ''
    if [ ! -d .venv ]; then
      echo "creating .venv and installing python deps (first run only)..."
      python -m venv .venv
      .venv/bin/pip install -q -r requirements.txt -r requirements-discord.txt
    fi
    export PATH="$PWD/.venv/bin:$PATH"
    echo "voice-agent shell ready (python from .venv, ffmpeg + opus from nix)"
  '';
}
