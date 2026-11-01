# SPDX-License-Identifier: MIT
#
# The library for evaluations outside a flake, such as the nix-build of a
# purifier's command: `import <library source> { inherit lib; }` is the
# attribute set that the flake has as `lib.purifier`. `lib` is a nixpkgs
# library.
{ lib }:
import ./nix/lib { inherit lib; }
