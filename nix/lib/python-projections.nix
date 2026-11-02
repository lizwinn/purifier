# SPDX-License-Identifier: MIT
{ lib }:
let
  inputIdentity = import ./input-identity.nix { inherit lib; };
  projectInputs = import ./project-inputs.nix { inherit lib; };
in
{
  repository,
  producer ? repository,
  runtimeRepository ? null,
  dependencies ? [ ],
  runCommand,
  python3,
  name ? "python-projection",
}:
let
  selected = projectInputs {
    inherit dependencies;
    formats = [ "python" ];
  };
  interpreter = python3.withPackages (packages: [ packages.packaging ]);
  inputs = {
    repository = [ (toString repository) ];
    runtime = lib.optional (runtimeRepository != null) (toString runtimeRepository);
    dependencies = map (input: toString input.repository) selected;
  };
  identity = runCommand "${name}-identity" {
    spec = builtins.toJSON inputs;
    producerIdentity = (inputIdentity producer).hash;
    projector = ../../tools/python_repository.py;
    nativeBuildInputs = [ interpreter ];
  } "touch $out";
  retained = [
    (toString producer)
  ]
  ++ lib.optional (runtimeRepository != null) (toString runtimeRepository)
  ++ map (input: toString input.repository) selected;
  projection =
    runCommand name
      {
        outputs = [
          "out"
          "metadata"
        ];
        nativeBuildInputs = [ interpreter ];
        passAsFile = [ "spec" ];
        spec = builtins.toJSON (inputs // { identity = (inputIdentity identity).hash; });
        passthru.catalog = "${projection.metadata}/catalog.json";
      }
      ''
        python ${../../tools/python_repository.py} --spec "$specPath" \
          --destination "$out" --catalog "$metadata/catalog.json"
        mkdir -p "$out/nix-support"
        printf '%s\n' ${lib.escapeShellArgs retained} > "$out/nix-support/inputs"
      '';
in
{
  python = projection.out;
}
