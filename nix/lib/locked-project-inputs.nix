# SPDX-License-Identifier: MIT
# Native lockfiles retain Nix's source identity, never duplicate provided JARs.
{ lib }:
{ inputs, system }:
map (
  input:
  let
    flake = builtins.getFlake input.url;
    path = lib.splitString "." (input.attribute or "default");
    packagePath = [
      "packages"
      system
    ]
    ++ path;
    legacyPath = [
      "legacyPackages"
      system
    ]
    ++ path;
  in
  assert flake.sourceInfo.narHash == input.narHash;
  if lib.hasAttrByPath packagePath flake then
    lib.getAttrFromPath packagePath flake
  else if lib.hasAttrByPath legacyPath flake then
    lib.getAttrFromPath legacyPath flake
  else
    lib.getAttrFromPath path flake
) inputs
