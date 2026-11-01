# SPDX-License-Identifier: MIT
#
# The functions of the `purifier` library namespace. README.md describes
# each of them.
{ lib }:
{
  fetchEntry = import ./fetch.nix { inherit lib; };
  tree = import ./tree.nix { inherit lib; };
  manifest = import ./manifest.nix { inherit lib; };
  inherit (import ./lock.nix { inherit lib; }) checkLock entriesFor;
  admits = import ./admits.nix { inherit lib; };
  wheels = import ./wheels.nix;
  wheelName = args: import ./wheel-name.nix ({ inherit lib; } // args);
}
