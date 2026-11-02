# SPDX-License-Identifier: MIT
# Identify an input through Nix's string context, rather than a native version.
# This reads derivation/output references without building or importing outputs.
{ lib }:
input:
let
  value = toString input;
  context = builtins.getContext value;
  references = builtins.attrNames context;
in
if references == [ ] then
  throw "purifier: a projected dependency must carry a Nix store or derivation reference"
else
  {
    inherit context;
    hash = builtins.hashString "sha256" (builtins.toJSON context);
  }
