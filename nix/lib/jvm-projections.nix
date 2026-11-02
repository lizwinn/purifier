# SPDX-License-Identifier: MIT
# Publish typed Maven/Ivy views of an already-built producer. The producer
# and projection are separate derivations, avoiding a hash referring to itself.
{ lib }:
let
  inputIdentity = import ./input-identity.nix { inherit lib; };
  projectInputs = import ./project-inputs.nix { inherit lib; };
in
{
  repository,
  producer ? repository,
  runtimeRepository ? null,
  formats ? [ "maven" ],
  dependencies ? [ ],
  runCommand,
  python3,
  name ? "jvm-projection",
}:
let
  selected = projectInputs {
    inherit dependencies;
    formats = [ "maven" ];
  };
  spec = builtins.toJSON {
    runtimeRepository = if runtimeRepository == null then null else toString runtimeRepository;
    repository = if repository == null then null else toString repository;
    producerHash = if repository == null then null else (inputIdentity producer).hash;
    dependencies = map (
      input:
      {
        maven = toString input.repository;
        catalog = input.repository.catalog;
      }
      // lib.optionalAttrs (input.producer.projections ? ivy) {
        ivy = toString input.producer.projections.ivy;
      }
    ) selected;
  };
  projection =
    runCommand name
      {
        outputs = [
          "out"
          "ivy"
          "metadata"
        ];
        nativeBuildInputs = [ python3 ];
        passAsFile = [ "spec" ];
        inherit spec;
        passthru = {
          catalog = "${projection.metadata}/catalog.json";
        };
      }
      ''
        python ${../../tools/jvm_repository.py} --spec "$specPath" \
          --maven "$out" --ivy "$ivy" --metadata "$metadata"
      '';
in
assert
  formats != [ ]
  && builtins.all (
    format:
    builtins.elem format [
      "maven"
      "ivy"
    ]
  ) formats;
lib.optionalAttrs (builtins.elem "maven" formats) {
  maven = projection.out;
}
// lib.optionalAttrs (builtins.elem "ivy" formats) {
  inherit (projection) ivy;
}
