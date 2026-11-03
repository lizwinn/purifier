# SPDX-License-Identifier: MIT
{ lib }:
let
  inputIdentity = import ./input-identity.nix { inherit lib; };
  projectInputs = import ./project-inputs.nix { inherit lib; };
in
{
  graph,
  archives,
  producer ? archives,
  roots ? null,
  dependencies ? [ ],
  runCommand,
  python3,
  name ? "npm-projection",
}:
let
  selected = projectInputs {
    inherit dependencies;
    formats = [ "npm" ];
  };
  retained = [
    (toString producer)
    (toString archives)
    (toString graph)
  ]
  ++ lib.concatMap (input: [
    (toString input.repository)
    (toString input.producer)
  ]) selected;
  toolchain = runCommand "npm-projector-toolchain" {
    nativeBuildInputs = [ python3 ];
    projector = ../../tools/npm_repository.py;
  } "touch $out";
  spec = {
    inherit graph;
    archives = toString archives;
    identity = (inputIdentity producer).hash;
    projectorIdentity = (inputIdentity toolchain).hash;
    dependencies = map (input: {
      repository = toString input.repository;
      catalog = input.repository.catalog;
    }) selected;
  }
  // lib.optionalAttrs (roots != null) { inherit roots; };
  projection =
    runCommand name
      {
        outputs = [
          "out"
          "metadata"
        ];
        nativeBuildInputs = [ python3 ];
        spec = builtins.toJSON spec;
        passAsFile = [ "spec" ];
        passthru.catalog = "${projection.metadata}/catalog.json";
      }
      ''
        python ${../../tools/npm_repository.py} --spec "$specPath" \
          --destination "$out" --catalog "$metadata/catalog.json"
        mkdir -p "$out/nix-support"
        printf '%s\n' ${lib.escapeShellArgs retained} > "$out/nix-support/inputs"
      '';
in
{
  npm = projection.out;
}
