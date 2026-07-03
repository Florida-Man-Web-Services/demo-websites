# System deps for the Discord voice bot on NixOS: ffmpeg (audio decode for
# playback) and libopus (Discord's voice codec). Python deps stay in the venv.
#
#   cd voice-agent
#   nix-shell --run '.venv/bin/python discord_bot.py'
{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages = with pkgs; [ ffmpeg libopus ];

  # discord.py can't find_library("opus") on NixOS; discord_bot.py reads this.
  DISCORD_OPUS_LIB = "${pkgs.libopus}/lib/libopus.so.0";
}
