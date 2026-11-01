# SPDX-License-Identifier: MIT
#
# A fetchurl that needs no nixpkgs: a fixed-output derivation on Nix's
# built-in fetcher, the derivation that <nix/fetchurl.nix> makes. It takes
# the arguments that fetchEntry gives nixpkgs' fetchurl for a file entry.
# A fixed-output store path depends only on the name, the hash and the
# hash mode, so it is the store path of nixpkgs' fetchurl with the same
# name and hash. Nix's built-in fetcher reads only `url`, so it downloads
# from the first of `urls` and never from the mirrors after it.
{
  name,
  urls,
  hash,
}:
derivation {
  inherit name urls;
  builder = "builtin:fetchurl";
  system = "builtin";
  url = builtins.head urls;
  outputHash = hash;
  outputHashMode = "flat";
  unpack = false;
  executable = false;
  preferLocalBuild = true;
  impureEnvVars = [
    "http_proxy"
    "https_proxy"
    "ftp_proxy"
    "all_proxy"
    "no_proxy"
  ];
}
