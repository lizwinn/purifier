# SPDX-License-Identifier: MIT
#
# `admits`: whether a package with npm's `os`, `cpu` and `libc` filters
# installs on a Nix system. Each filter is null, a name or a list of
# names, with npm's meanings:
#
#   - null, an empty list and the list [ "any" ] admit every system;
#   - a name listed with a leading "!" refuses the system of that name;
#   - a list with names that have no "!" admits only the systems it names;
#   - a list of only names with "!" admits every system it does not name.
#
# The systems are those of the nix-purifiers family.
{ lib }:
let
  inherit (builtins)
    all
    elem
    filter
    isList
    isString
    length
    toJSON
    ;

  platforms = {
    x86_64-linux = {
      os = "linux";
      cpu = "x64";
      libc = "glibc";
    };
    aarch64-linux = {
      os = "linux";
      cpu = "arm64";
      libc = "glibc";
    };
  };

  allows =
    field: value: wanted:
    let
      list =
        if wanted == null then
          [ ]
        else if isString wanted then
          [ wanted ]
        else if isList wanted && all isString wanted then
          wanted
        else
          throw "purifier: admits: the `${field}` filter ${toJSON wanted} is not null, a string or a list of strings";
      negated = filter (lib.hasPrefix "!") list;
    in
    list == [ "any" ]
    || (!(elem "!${value}" list) && (elem value list || length negated == length list));
in
{
  os,
  cpu,
  libc,
}:
system:
let
  platform =
    platforms.${system}
      or (throw "purifier: admits knows the systems ${toJSON (builtins.attrNames platforms)}, and not ${toJSON system}");
in
# An unknown system fails even when no filter needs its names.
builtins.seq platform (
  allows "os" platform.os os && allows "cpu" platform.cpu cpu && allows "libc" platform.libc libc
)
