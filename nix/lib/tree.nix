# SPDX-License-Identifier: MIT
#
# `tree`: download entries laid out as one directory. Each entry's store
# path lies at the entry's `path`: as a symbolic link into the store when
# `link` is true, and as a copy when it is false, for the tools that
# ignore files behind symbolic links. Two entries at the same path fail
# evaluation, and so does an entry inside the path of another.
#
# The arguments besides `name`, `entries` and `link` go to fetchEntry:
# `pkgs`, or the fetchers and packages it takes by name. `tree` builds the
# directory with `runCommand`, given by name or taken from `pkgs`.
{ lib }:
let
  entriesLib = import ./entries.nix { inherit lib; };
  fetchEntry = import ./fetch.nix { inherit lib; };

  inherit (builtins)
    concatStringsSep
    filter
    genList
    groupBy
    length
    split
    toJSON
    ;

  # The directories above a path: "a/b/c" has "a" and "a/b".
  ancestors =
    path:
    let
      components = filter builtins.isString (split "/" path);
      count = length components;
    in
    genList (n: concatStringsSep "/" (lib.take (n + 1) components)) (count - 1);

  # The problem with the paths of checked entries, or null.
  pathsProblem =
    entries:
    let
      paths = map (entry: entry.path) entries;
      byPath = groupBy (path: path) paths;
      duplicates = filter (path: length byPath.${path} > 1) (builtins.attrNames byPath);
      nested = filter (path: builtins.any (ancestor: byPath ? ${ancestor}) (ancestors path)) paths;
      outer = path: lib.findFirst (ancestor: byPath ? ${ancestor}) null (ancestors path);
    in
    if duplicates != [ ] then
      "purifier: more than one entry has the path ${toJSON (builtins.head duplicates)}"
    else if nested != [ ] then
      "purifier: the entry at path ${toJSON (builtins.head nested)} lies inside the entry at path ${toJSON (outer (builtins.head nested))}"
    else
      null;
in
args@{
  name,
  entries,
  link ? true,
  ...
}:
let
  fetchers = removeAttrs args [
    "name"
    "entries"
    "link"
  ];
  runCommand =
    fetchers.runCommand or fetchers.pkgs.runCommand
      or (throw "purifier: tree ${toJSON name} needs `runCommand` or `pkgs`");
  fetch = fetchEntry fetchers;

  checked = map entriesLib.check entries;
  problem = pathsProblem checked;

  fetched = map (entry: {
    inherit (entry) path;
    source = fetch entry;
  }) checked;
  provenance = builtins.listToAttrs (
    map (entry: {
      name = entry.path;
      value = entry.source;
    }) fetched
  );

  place = if link then "ln -s --" else "copy";
in
if problem != null then
  throw problem
else
  runCommand name
    {
      # The links point at their fetches; a copy is no cheaper to fetch
      # from a cache than to make.
      preferLocalBuild = true;
      allowSubstitutes = false;
      # Retain the source derivations even when native tools require copies.
      # This is evaluation metadata, not an extra file in the repository.
      passthru = { inherit provenance; };
      passAsFile = [ "placements" ];
      placements = concatStringsSep "" (
        map (entry: "place ${entry.source} ${lib.escapeShellArg entry.path}\n") fetched
      );
    }
    ''
      copy() {
        cp -R --no-preserve=ownership -- "$1" "$2"
        chmod -R u+w -- "$2"
      }
      place() {
        mkdir -p -- "$out/$(dirname -- "$2")"
        ${place} "$1" "$out/$2"
      }
      mkdir -p "$out"
      source "$placementsPath"
    ''
