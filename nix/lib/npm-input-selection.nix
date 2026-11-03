# SPDX-License-Identifier: MIT
{ lib }:
{ dependencies }:
let
  inputs = (import ./project-inputs.nix { inherit lib; }) {
    inherit dependencies;
    formats = [ "npm" ];
  };
  views = map (input: {
    repository = toString input.repository;
    catalog = input.repository.catalog;
  }) inputs;
  modules = lib.concatMap (view: (builtins.fromJSON (builtins.readFile view.catalog)).modules) views;
  grouped = lib.groupBy (item: item.name) modules;
in
{
  inherit inputs views;
  names = builtins.attrNames (
    lib.filterAttrs (
      _: values:
      builtins.any (item: item.primary or false) values
      || builtins.length (lib.unique (map (item: item.version) values)) == 1
    ) grouped
  );
  versions = map (item: { inherit (item) name version; }) modules;
}
