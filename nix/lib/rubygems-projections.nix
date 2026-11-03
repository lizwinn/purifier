# SPDX-License-Identifier: MIT
{ lib }:
let
  inputIdentity = import ./input-identity.nix { inherit lib; };
  projectInputs = import ./project-inputs.nix { inherit lib; };
in
{
  archives,
  producer,
  roots ? null,
  dependencies ? [ ],
  ruby,
  runCommand,
  name ? "rubygems-projection",
}:
let
  selected = projectInputs {
    inherit dependencies;
    formats = [ "rubygems" ];
  };
  repositories = map (input: {
    repository = toString input.repository;
    catalog = input.repository.catalog;
  }) selected;
  toolchain = runCommand "rubygems-projector-toolchain" {
    projector = ../../tools/rubygems_repository.rb;
    nativeBuildInputs = [ ruby ];
  } "touch $out";
  archiveIdentities = builtins.listToAttrs (
    map (archive: {
      name = builtins.unsafeDiscardStringContext (toString archive);
      value =
        (inputIdentity (
          builtins.path {
            path = archive;
            name = "gem";
          }
        )).hash;
    }) archives
  );
  identity = runCommand "${name}-identity" {
    producerIdentity = (inputIdentity producer).hash;
    projector = ../../tools/rubygems_repository.rb;
    nativeBuildInputs = [ ruby ];
    archives = builtins.toJSON archives;
    dependencies = builtins.toJSON repositories;
  } "touch $out";
  projection =
    runCommand name
      {
        outputs = [
          "out"
          "metadata"
        ];
        nativeBuildInputs = [ ruby ];
        spec = builtins.toJSON {
          identity = (inputIdentity identity).hash;
          inherit archives archiveIdentities roots;
          projectorIdentity = (inputIdentity toolchain).hash;
          dependencies = repositories;
        };
        passAsFile = [ "spec" ];
        passthru.catalog = "${projection.metadata}/catalog.json";
      }
      ''
        ruby ${../../tools/rubygems_repository.rb} "$specPath" \
          "$out" "$metadata/catalog.json"
        mkdir -p "$out/nix-support"
        printf '%s\n' ${
          lib.escapeShellArgs (
            [ (toString producer) ]
            ++ lib.concatMap (input: [
              (toString input.repository)
              (toString input.producer)
            ]) selected
          )
        } \
          > "$out/nix-support/inputs"
      '';
in
{
  rubygems = projection.out;
}
