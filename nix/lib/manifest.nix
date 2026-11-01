# SPDX-License-Identifier: MIT
#
# `manifest`: a store file that maps the `path` of each file and inline
# entry to its store path, with no nixpkgs. The file refers to every path
# it names, so one garbage-collector root on it keeps them all alive.
#
# A file made with builtins.toFile may refer to store paths but not to the
# outputs of derivations. So the manifest names the fetch of a file entry
# through builtins.fetchurl with the name and hash that fetchEntry gives
# it, which is the same fixed-output store path. A caller builds the
# fetches first (fetchEntry with no `pkgs`), and builtins.fetchurl then
# finds each path valid and downloads nothing.
{ lib }:
let
  entriesLib = import ./entries.nix { inherit lib; };
  fetchEntry = import ./fetch.nix { inherit lib; };

  storePath =
    entry:
    let
      kind = entriesLib.kindOf entry;
    in
    if kind == "file" then
      builtins.fetchurl {
        inherit (entry) url;
        name = entriesLib.storeName (baseNameOf entry.path);
        sha256 = entry.hash;
      }
    else if kind == "inline" then
      fetchEntry { } entry
    else
      throw "purifier: manifest takes file and inline entries, and the entry at path ${builtins.toJSON entry.path} is a ${kind} entry";
in
{ name, entries }:
let
  checked = map entriesLib.check entries;
in
builtins.toFile name (
  builtins.toJSON (
    builtins.listToAttrs (
      map (entry: {
        name = entry.path;
        value = storePath entry;
      }) checked
    )
  )
)
