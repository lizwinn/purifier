# SPDX-License-Identifier: MIT
# Native lockfiles retain Nix's source identity, never duplicate provided JARs.
{ lib }:
{ inputs, system }:
map (
  input:
  let
    source = builtins.fetchTree (
      removeAttrs (builtins.parseFlakeRef input.url) [ "dir" ] // { narHash = input.narHash; }
    );
    lockedUrl =
      input.url
      + lib.optionalString (!(lib.hasInfix "narHash=" input.url)) (
        (if lib.hasInfix "?" input.url then "&" else "?") + "narHash=" + input.narHash
      );
    flake = builtins.getFlake lockedUrl;
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
  assert source.narHash == input.narHash;
  if lib.hasAttrByPath packagePath flake then
    lib.getAttrFromPath packagePath flake
  else if lib.hasAttrByPath legacyPath flake then
    lib.getAttrFromPath legacyPath flake
  else
    lib.getAttrFromPath path flake
) inputs
