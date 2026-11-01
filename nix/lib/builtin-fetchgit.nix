# SPDX-License-Identifier: MIT
#
# A fetchgit that needs no nixpkgs, on builtins.fetchGit. It takes the
# arguments that fetchEntry gives nixpkgs' fetchgit for a git entry, and
# checks the checkout against `hash` as its narHash. The checkout has no
# .git directory, as nixpkgs' fetchgit gives it with leaveDotGit false,
# so `hash` is the hash that nix-prefetch-git reports for it. Its store
# path differs from the one nixpkgs' fetchgit gives, because
# builtins.fetchGit names it `source`.
{
  url,
  rev,
  hash,
  fetchSubmodules,
  leaveDotGit,
  ...
}:
assert !leaveDotGit;
builtins.fetchGit {
  inherit url rev;
  narHash = hash;
  submodules = fetchSubmodules;
  allRefs = true;
}
