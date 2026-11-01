# SPDX-License-Identifier: MIT
#
# `purifier-packages`: a purifier's flake publishes as `packages` the
# derivations of its scope, `pkgs.<scope>`. The scope also holds the builder,
# a function, which a flake's `packages` cannot hold. A flake that adds more
# packages, such as `default`, sets them beside these.
{ ... }:
{ config, lib, ... }:
{
  options.purifier.scope = lib.mkOption {
    type = lib.types.str;
    example = "cargo-purifier";
    description = "The name of the purifier's scope in the package set.";
  };

  config = {
    caisson.nixpkgs.packages.export.enabled = false;
    perSystem =
      { pkgs, ... }:
      {
        packages = lib.filterAttrs (_: lib.isDerivation) pkgs.${config.purifier.scope};
      };
  };
}
