{
  description = "tgexport — personal Telegram account archiver";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python312;
      in
      {
        # Python dependencies are managed by uv (pyproject.toml / uv.lock):
        #   uv sync --all-extras          # install everything into .venv
        #   uv run tgexport ...           # run the CLI
        #   uv run pytest / mypy / ruff   # dev tools
        devShells.default = pkgs.mkShell {
          packages = [
            pkgs.uv
            python
            pkgs.sqlite
            # PyPI ruff ships a dynamically linked binary that NixOS can't run.
            pkgs.ruff
          ];
          env = {
            # Use the nix-provided interpreter; uv-downloaded binaries don't
            # run on NixOS.
            UV_PYTHON = python.interpreter;
            UV_PYTHON_DOWNLOADS = "never";
          };
          # manylinux wheels (PyQt5, cryptg) need libstdc++/zlib at runtime,
          # which NixOS does not provide globally.
          shellHook = ''
            export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib pkgs.zlib pkgs.glib ]}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
          '';
        };
      });
}
