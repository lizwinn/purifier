# SPDX-License-Identifier: MIT
#
# `fetchEntry`: one download entry (entries.nix) to a store path.
#
#   file     fetchurl from `url`, then from each of `mirrors`
#   inline   builtins.toFile with `contents`
#   git      fetchgit at `git.rev`, with submodules when `git.submodules`
#            is true and the .git directory when `git.leaveDotGit` is true,
#            so `hash` is the hash that nix-prefetch-git reports
#   archive  fetchurl of the archive file, whose hash is `hash`, unpacked
#            in a derivation; with `archive.stripTop` the single top-level
#            directory of the archive is the result
#   hg       fetchhg, with subrepositories when `hg.subrepos` is true
#   svn      fetchsvn, without externals when `svn.ignoreExternals` is
#            true and without keyword expansion when
#            `svn.ignoreKeywords` is true
#   fossil   fetchfossil
#   bzr      fetchbzr
#   darcs    fetchdarcs at the tag `darcs.rev`, or at the version that the
#            text of the context file `darcs.context` names
#   cvs      fetchcvs at `cvs.tag` or at `cvs.date`
#
# Every fetcher is nixpkgs' by default. A purifier passes its own in place
# of one, such as a fetchurl that sends credentials; a replacement takes
# the arguments that the nixpkgs fetcher of its name takes here.
{ lib }:
let
  entries = import ./entries.nix { inherit lib; };
  inherit (entries) storeName;

  # The last segment of a URL, without its query or fragment.
  urlBaseName = url: baseNameOf (builtins.head (builtins.split "[?#]" url));
in
{
  pkgs,
  fetchurl ? pkgs.fetchurl,
  fetchgit ? pkgs.fetchgit,
  fetchhg ? pkgs.fetchhg,
  fetchsvn ? pkgs.fetchsvn,
  fetchfossil ? pkgs.fetchfossil,
  fetchbzr ? pkgs.fetchbzr,
  fetchdarcs ? pkgs.fetchdarcs,
  fetchcvs ? pkgs.fetchcvs,
}:
entry:
let
  checked = entries.check entry;
  kind = entries.kindOf checked;
  name = storeName (baseNameOf checked.path);
  inherit (checked) path;

  unpack =
    {
      url,
      mirrors,
      stripTop,
    }:
    pkgs.runCommand name
      {
        nativeBuildInputs = [
          pkgs.unzip
          pkgs.zstd
        ];
        src = fetchurl {
          name = storeName (urlBaseName url);
          urls = [ url ] ++ mirrors;
          inherit (checked) hash;
        };
        entryPath = path;
        inherit stripTop;
      }
      ''
        mkdir unpacked
        # A zip archive starts with "PK"; tar reads every other format the
        # archives come in, and finds their compression itself.
        if [ "$(head -c 2 "$src")" = PK ]; then
          unzip -q "$src" -d unpacked
        else
          tar -xf "$src" -C unpacked --no-same-owner --warning=no-timestamp
        fi
        chmod -R u+w unpacked
        if [ -n "$stripTop" ]; then
          shopt -s dotglob nullglob
          top=(unpacked/*)
          if [ "''${#top[@]}" -ne 1 ] || [ -L "''${top[0]}" ] || [ ! -d "''${top[0]}" ]; then
            echo "purifier: the archive of the entry at path \"$entryPath\" has ''${#top[@]} top-level entries, and stripTop needs exactly one directory:" >&2
            ls -A unpacked >&2
            exit 1
          fi
          mv "''${top[0]}" "$out"
        else
          mv unpacked "$out"
        fi
      '';

  fetchers = {
    file = fetchurl {
      inherit name;
      urls = [ checked.url ] ++ checked.mirrors;
      inherit (checked) hash;
    };

    inline = builtins.toFile name checked.contents;

    git = fetchgit {
      inherit name;
      inherit (checked.git) url rev leaveDotGit;
      fetchSubmodules = checked.git.submodules;
      inherit (checked) hash;
    };

    archive = unpack checked.archive;

    hg = fetchhg {
      inherit name;
      inherit (checked.hg) url rev;
      fetchSubrepos = checked.hg.subrepos;
      inherit (checked) hash;
    };

    svn = fetchsvn {
      inherit name;
      inherit (checked.svn)
        url
        rev
        ignoreExternals
        ignoreKeywords
        ;
      inherit (checked) hash;
    };

    fossil = fetchfossil {
      inherit name;
      inherit (checked.fossil) url rev;
      inherit (checked) hash;
    };

    # A Darcs entry names its version by a tag (`rev`) or by the text of a
    # context file (`context`), which fetchdarcs takes as a file.
    darcs = fetchdarcs (
      {
        inherit name;
        inherit (checked.darcs) url;
        inherit (checked) hash;
      }
      // lib.optionalAttrs (checked.darcs.rev != null) { inherit (checked.darcs) rev; }
      // lib.optionalAttrs (checked.darcs.context != null) {
        context = pkgs.writeText "${name}-context" checked.darcs.context;
      }
    );

    # nixpkgs' fetchbzr and fetchcvs take no name.
    bzr = fetchbzr {
      inherit (checked.bzr) url rev;
      inherit (checked) hash;
    };

    cvs = fetchcvs (
      {
        inherit (checked.cvs) cvsRoot module;
        inherit (checked) hash;
      }
      // lib.optionalAttrs (checked.cvs.tag != null) { inherit (checked.cvs) tag; }
      // lib.optionalAttrs (checked.cvs.date != null) { inherit (checked.cvs) date; }
    );
  };
in
fetchers.${kind}
