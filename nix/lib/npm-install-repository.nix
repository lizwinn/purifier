# SPDX-License-Identifier: MIT
{ lib }:
{
  dependencies,
  runCommand,
  python3,
  name ? "npm-install-repository",
}:
let
  selected = (import ./project-inputs.nix { inherit lib; }) {
    inherit dependencies;
    formats = [ "npm" ];
  };
  retained = lib.concatMap (input: [
    (toString input.repository)
    (toString input.producer)
  ]) selected;
  result =
    runCommand name
      {
        outputs = [
          "out"
          "metadata"
        ];
        nativeBuildInputs = [ python3 ];
        spec = builtins.toJSON {
          views = map (input: {
            repository = toString input.repository;
            catalog = input.repository.catalog;
          }) selected;
        };
        passAsFile = [ "spec" ];
        passthru.catalog = "${result.metadata}/catalog.json";
      }
      ''
        python ${../../tools/npm_repository.py} --install --spec "$specPath" \
          --destination "$out" --catalog "$metadata/catalog.json"
        mkdir -p "$out/nix-support"
        printf '%s\n' ${lib.escapeShellArgs retained} > "$out/nix-support/inputs"
      '';
in
result.out
