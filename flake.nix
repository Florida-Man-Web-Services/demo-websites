{
  description = "Gainesville demo websites: static demo sites, AI voice sales agent, MCP server";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems
        (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs: rec {
        # The component shell.nix files stay usable standalone via nix-shell;
        # the flake just pins nixpkgs and exposes them as `nix develop` targets.
        voice-agent = import ./voice-agent/shell.nix { inherit pkgs; };
        mcp-server = import ./mcp-server/shell.nix { inherit pkgs; };
        default = voice-agent;
      });
    };
}
