# SPDX-License-Identifier: MIT
# Select a native projection from each declared Nix dependency. The consumer
# lists formats it understands, in preference order; users supply packages,
# not version-rewrite rules. Native coordinates are read by the format adapter.
{ lib }:
let
  inputIdentity = import ./input-identity.nix { inherit lib; };
in
{
  dependencies,
  formats,
}:
assert
  formats != [ ] || throw "purifier: a projection consumer must name at least one supported format";
map (
  dependency:
  let
    projections = dependency.projections or { };
    available = builtins.filter (format: projections ? ${format}) formats;
    format = builtins.head available;
    repository = projections.${format};
    identity = inputIdentity dependency;
    repositoryIdentity = inputIdentity repository;
  in
  if available == [ ] then
    throw "purifier: a Nix dependency has no projection in a supported format (${builtins.concatStringsSep ", " formats})"
  else
    {
      inherit
        format
        repository
        identity
        repositoryIdentity
        ;
      # Keep the original derivation as an input too: the projection is a view
      # of that dependency, not a new, independently resolved dependency.
      producer = dependency;
    }
) dependencies
