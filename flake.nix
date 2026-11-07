{
  description = "mortocs-tech-art-agent: one agent core for Houdini, Blender and games (pydantic-ai, FastAPI)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, pyproject-nix, uv2nix, pyproject-build-systems }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./core; };
      overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };

      pythonSetFor = pkgs:
        (pkgs.callPackage pyproject-nix.build.packages { python = pkgs.python313; }).overrideScope
          (nixpkgs.lib.composeManyExtensions [ pyproject-build-systems.overlays.default overlay ]);
    in
    {
      packages = forAll (pkgs:
        let
          venv = (pythonSetFor pkgs).mkVirtualEnv "tech-art-core-env" workspace.deps.default;

          # The pane UI (React). Only protocol/ and ui/ are built; the old Node sidecar is gone.
          ui = pkgs.buildNpmPackage {
            pname = "tech-art-agent-ui";
            version = "0.1.0";
            src = self;
            npmDepsHash = "sha256-AtoyFyvV1eRKgGc2R50mHPIeScWDvqmSb/FJ1/YTYVw=";
            nodejs = pkgs.nodejs_22;
            PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1";
            buildPhase = "npm run build -w protocol && npm run build -w ui";
            installPhase = "mkdir -p $out && cp -r ui/dist/. $out/";
          };
        in
        rec {
          inherit ui;

          # The core with the UI path and uvx baked in. Run: tech-art-core
          tech-art-core = pkgs.runCommand "tech-art-core" { nativeBuildInputs = [ pkgs.makeWrapper ]; } ''
            mkdir -p $out/bin
            makeWrapper ${venv}/bin/tech-art-core $out/bin/tech-art-core \
              --set-default TECH_ART_AGENT_UI ${ui} \
              --set-default TECH_ART_AGENT_UVX ${pkgs.uv}/bin/uvx \
              --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.uv pkgs.git ]}
          '';

          # The Houdini side, loaded by HOUDINI_PATH from a package file: houdini/ plus the shared client library.
          houdini-plugin = pkgs.runCommand "tech-art-agent-houdini" { } ''
            mkdir -p $out
            cp -r ${./houdini} $out/houdini
            cp -r ${./python} $out/python
          '';

          # Install with home-manager:
          #   home.file."houdini22.0/packages/tech_art_agent.json".source = tech-art-agent-houdini-package;
          tech-art-agent-houdini-package = pkgs.writeText "tech_art_agent.json" (builtins.toJSON {
            _houdini_pi_installer = true;
            env = [
              { TECH_ART_AGENT_ROOT = "${houdini-plugin}"; }
              { PYTHONPATH = { method = "append"; value = "$TECH_ART_AGENT_ROOT/python"; }; }
            ];
            path = "$TECH_ART_AGENT_ROOT/houdini";
          });

          default = tech-art-core;
        });

      nixosModules.default = import ./nix/module.nix { inherit self; };

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ pkgs.python313 pkgs.uv pkgs.nodejs_22 pkgs.git ];
          UV_PYTHON_DOWNLOADS = "never";
        };
      });
    };
}
