# SPDX-License-Identifier: MIT
#
# The lock in memory: the attribute set that every purifier reads its
# lockfiles into, whatever their formats. CONTRACT.md describes it.
#
#   {
#     purifier = "<tool>-purifier";
#     entries  = [ <download entry> ];  # entries.nix; each may carry
#                                       # `systems`, a list of Nix systems,
#                                       # or null for every system
#     outputs  = { <name> = { ... }; }; # the outputs, named at evaluation
#     problems = [ <string> ];          # why the lockfiles cannot be used,
#                                       # each naming the command that fixes it
#   }
#
# `checkLock` returns the lock unchanged when it has this shape and no
# problems, and fails evaluation otherwise. `entriesFor` gives the entries
# of one system.
{ lib }:
let
  entriesLib = import ./entries.nix { inherit lib; };

  inherit (builtins)
    concatStringsSep
    filter
    isAttrs
    isList
    isString
    all
    elem
    ;

  keys = [
    "purifier"
    "entries"
    "outputs"
    "problems"
  ];

  shapeProblem =
    lock:
    if !isAttrs lock then
      "the lock is not an attribute set"
    else if filter (key: !(lock ? ${key})) keys != [ ] then
      "the lock has no `${builtins.head (filter (key: !(lock ? ${key})) keys)}`"
    else if !isString lock.purifier then
      "`purifier` is not a string"
    else if !isList lock.entries then
      "`entries` is not a list"
    else if !isAttrs lock.outputs then
      "`outputs` is not an attribute set"
    else if !(isList lock.problems && all isString lock.problems) then
      "`problems` is not a list of strings"
    else
      let
        badSystems = filter (
          entry: (entry.systems or null) != null && !(isList entry.systems && all isString entry.systems)
        ) lock.entries;
      in
      if badSystems != [ ] then
        "the entry at path ${
          builtins.toJSON (builtins.head badSystems).path or null
        } has `systems` that is neither null nor a list of strings"
      else
        null;

  # The problems are reported before `entries` is evaluated, because a
  # purifier may build its entries from a lockfile that its problems say
  # cannot be read.
  checkLock =
    lock:
    let
      problem = shapeProblem lock;
      wrongShape = "purifier: ${lock.purifier or "a purifier"} gave a lock of the wrong shape: ";
    in
    if !(isAttrs lock && lock ? problems && isList lock.problems && all isString lock.problems) then
      throw "${wrongShape}`problems` is missing or not a list of strings"
    else if lock.problems != [ ] then
      throw (concatStringsSep "\n" lock.problems)
    else if problem != null then
      throw "${wrongShape}${problem}"
    else
      lib.foldr (entry: checked: builtins.seq (entriesLib.check entry) checked) lock lock.entries;

  entriesFor =
    system: lock:
    filter (entry: (entry.systems or null) == null || elem system entry.systems)
      (checkLock lock).entries;
in
{
  inherit checkLock entriesFor;
}
