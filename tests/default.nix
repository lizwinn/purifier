# SPDX-License-Identifier: MIT
#
# The checks: the tests that need no build (evaluation.nix), and builds
# that fetch a real file, git checkout and archive and lay them out as a
# tree.
{ pkgs, purifier }:
let
  inherit (pkgs) lib runCommand;

  # A file of octocat/Hello-World on GitHub, at a fixed commit.
  file = {
    path = "files/README";
    url = "https://raw.githubusercontent.com/octocat/Hello-World/7fd1a60b01f91b314f59955a4e4d4e80d8edf11d/README";
    mirrors = [ ];
    hash = "sha256-A7ogTlDRJuRnTABeBNguhMITZngK8fQ71Uo3gWtqs0A=";
  };

  # The same repository, checked out at that commit.
  git = {
    path = "sources/hello-world";
    git = {
      url = "https://github.com/octocat/Hello-World.git";
      rev = "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d";
      submodules = false;
      leaveDotGit = false;
    };
    hash = "sha256-gdkPz7VJ8ZOwJ5oetnuXBXPkkHlOsU7w0PghYjWgpAo=";
  };

  # The release tarball of GNU hello 2.12.1, whose single top-level
  # directory is hello-2.12.1.
  archive = stripTop: {
    path = if stripTop then "sources/hello" else "sources/hello-unstripped";
    archive = {
      url = "https://ftpmirror.gnu.org/hello/hello-2.12.1.tar.gz";
      mirrors = [ "https://ftp.gnu.org/gnu/hello/hello-2.12.1.tar.gz" ];
      inherit stripTop;
    };
    hash = "sha256-jZkUKv2SV28wsM18tCqNxoCZmLxdYH2Idh9RLibH2yA=";
  };

  inline = {
    path = "files/note.txt";
    contents = "laid out by purifier\n";
  };

  entries = [
    file
    git
    (archive true)
    (archive false)
    inline
  ];

  tree =
    link:
    purifier.tree {
      inherit pkgs entries link;
      name = "purifier-test-tree-${if link then "linked" else "copied"}";
    };

  # A tar archive with two top-level directories, which a replacement
  # fetchurl serves in place of a download.
  twoTops = runCommand "two-tops.tar" { } ''
    mkdir -p a b
    echo a > a/file
    echo b > b/file
    tar -cf "$out" a b
  '';
in
{
  evaluation = import ./evaluation.nix { inherit pkgs purifier; };

  fetch-file = runCommand "purifier-test-fetch-file" { } ''
    grep -q "Hello World" ${purifier.fetchEntry { inherit pkgs; } file}
    touch "$out"
  '';

  fetch-git = runCommand "purifier-test-fetch-git" { } ''
    src=${purifier.fetchEntry { inherit pkgs; } git}
    grep -q "Hello World" "$src/README"
    test ! -e "$src/.git"
    touch "$out"
  '';

  fetch-archive = runCommand "purifier-test-fetch-archive" { } ''
    stripped=${purifier.fetchEntry { inherit pkgs; } (archive true)}
    unstripped=${purifier.fetchEntry { inherit pkgs; } (archive false)}
    test -x "$stripped/configure"
    test -x "$unstripped/hello-2.12.1/configure"
    test "$(ls -A "$unstripped")" = hello-2.12.1
    touch "$out"
  '';

  # stripTop fails the build of an archive with two top-level entries. The
  # replacement fetchurl serves the archive.
  fetch-archive-two-tops = pkgs.testers.testBuildFailure (
    purifier.fetchEntry
      {
        inherit pkgs;
        fetchurl = _: twoTops;
      }
      {
        path = "two-tops";
        archive = {
          url = "https://example.org/two-tops.tar";
          mirrors = [ ];
          stripTop = true;
        };
        hash = lib.fakeHash;
      }
  );

  tree-linked = runCommand "purifier-test-tree-linked" { } ''
    t=${tree true}
    for p in files/README sources/hello-world sources/hello sources/hello-unstripped files/note.txt; do
      test -L "$t/$p"
    done
    grep -q "Hello World" "$t/files/README"
    test -x "$t/sources/hello/configure"
    test "$(cat "$t/files/note.txt")" = "laid out by purifier"
    touch "$out"
  '';

  tree-copied = runCommand "purifier-test-tree-copied" { } ''
    t=${tree false}
    test -n "$(find "$t" -type l)" && { echo "a copied tree has links" >&2; exit 1; }
    test -f "$t/files/README"
    test -d "$t/sources/hello-world"
    test -x "$t/sources/hello/configure"
    test -x "$t/sources/hello-unstripped/hello-2.12.1/configure"
    test "$(cat "$t/files/note.txt")" = "laid out by purifier"
    touch "$out"
  '';
}
