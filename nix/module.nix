# NixOS module: the tech-art agent core as a socket-activated user service.
#
#   services.tech-art-agent = {
#     enable = true;
#     # sops-nix: a dotenv-format secret containing PYDANTIC_AI_GATEWAY_API_KEY=...
#     environmentFile = config.sops.secrets."tech-art-agent.env".path;
#   };
#
# Nothing runs until something (Houdini, Blender, the pane) connects to the socket. The core then
# exits after `idleTimeout` seconds with no app, pane or running turn, and systemd wakes it again.
{ self }:
{ config, lib, pkgs, ... }:
let
  cfg = config.services.tech-art-agent;
  inherit (lib) mkEnableOption mkIf mkOption types;
in
{
  options.services.tech-art-agent = {
    enable = mkEnableOption "the tech-art agent core (pydantic-ai service for Houdini, Blender and games)";

    package = mkOption {
      type = types.package;
      default = self.packages.${pkgs.system}.tech-art-core;
      defaultText = "tech-art-agent flake's tech-art-core";
      description = "The core, with the pane UI bundled.";
    };

    port = mkOption {
      type = types.port;
      default = 9100;
      description = "Loopback port apps and the pane connect to. The socket is bound by systemd, on 127.0.0.1 only.";
    };

    environmentFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = "/run/secrets/tech-art-agent.env";
      description = ''
        A file of KEY=value lines read by the service, for the model key (PYDANTIC_AI_GATEWAY_API_KEY for the
        Pydantic AI Gateway; OPENROUTER_API_KEY and others also work) and LOGFIRE_TOKEN. With sops-nix, point this at a
        secret the user can read.
      '';
    };

    idleTimeout = mkOption {
      type = types.ints.unsigned;
      default = 600;
      description = "Seconds with no app, pane or running turn before the core exits. 0: never.";
    };

    sandbox = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Confine the core's disk access. The agent has no file or shell tool; this enforces the same at the
        OS level: the home directory is hidden except the state directory (~/.tech-art-agent, and the old
        ~/.houdini-pi if it exists) and uv's caches (the MCP servers run through uvx); everything else is
        read-only. /tmp stays readable because apps hand the core screenshots as files there.
      '';
    };

    extraEnvironment = mkOption {
      type = types.attrsOf types.str;
      default = { };
      description = "Extra environment for the service (no secrets: those go in environmentFile).";
    };
  };

  config = mkIf cfg.enable {
    systemd.user.sockets.tech-art-agent = {
      description = "Tech-art agent core socket";
      wantedBy = [ "sockets.target" ];
      socketConfig = {
        ListenStream = "127.0.0.1:${toString cfg.port}";
        # One shared service; do not spawn one per connection.
        Accept = false;
      };
    };

    systemd.user.services.tech-art-agent = {
      description = "Tech-art agent core";
      # Woken by the socket, never started at login.
      environment = {
        TECH_ART_AGENT_IDLE_TIMEOUT = toString cfg.idleTimeout;
        TECH_ART_AGENT_UVX = "${pkgs.uv}/bin/uvx";
        PYDANTIC_AI_NO_BANNER = "1";
      } // cfg.extraEnvironment;
      path = [ pkgs.uv pkgs.git ]; # uvx runs the MCP servers (fxhoudinimcp from git, mcp-for-blender from PyPI)
      serviceConfig = {
        ExecStart = "${cfg.package}/bin/tech-art-core";
        Restart = "on-failure";
        NoNewPrivileges = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
        RestrictNamespaces = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectControlGroups = true;
        MemoryHigh = "1G";
        TasksMax = 256;
      }
      // lib.optionalAttrs (cfg.environmentFile != null) { EnvironmentFile = cfg.environmentFile; }
      // lib.optionalAttrs cfg.sandbox {
        ProtectSystem = "strict";
        ProtectHome = "tmpfs";
        # A "-" prefix skips a path that does not exist yet.
        BindPaths = [ "-%h/.tech-art-agent" "-%h/.houdini-pi" "-%h/.cache/uv" "-%h/.local/share/uv" ];
      };
    };
  };
}
