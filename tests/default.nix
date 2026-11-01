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
      commitObject = null;
    };
    hash = "sha256-gdkPz7VJ8ZOwJ5oetnuXBXPkkHlOsU7w0PghYjWgpAo=";
  };

  # The same checkout with a .git directory, rebuilt from the raw commit
  # object of the commit (`git cat-file commit <rev> | base64 -w0`).
  # `hash` stays the hash of the plain checkout.
  commitObject = "dHJlZSBiNGVlY2FmYTliZTJmMjAwNmNlMWI3MDlkNjg1N2IwNzA2OWI0NjA4CnBhcmVudCA1NTNjMjA3N2YwZWRjM2Q1ZGM1ZDE3MjYyZjZhYTQ5OGU2OWQ2ZjhlCnBhcmVudCA3NjI5NDEzMThlZTE2ZTU5ZGFiYmFjYjFiNDA0OWVlYzIyZjBkMzAzCmF1dGhvciBUaGUgT2N0b2NhdCA8b2N0b2NhdEBub3doZXJlLmNvbT4gMTMzMTA3NTIxMCAtMDgwMApjb21taXR0ZXIgVGhlIE9jdG9jYXQgPG9jdG9jYXRAbm93aGVyZS5jb20+IDEzMzEwNzUyMTAgLTA4MDAKCk1lcmdlIHB1bGwgcmVxdWVzdCAjNiBmcm9tIFNwYWNlZ2hvc3QvcGF0Y2gtMQoKTmV3IGxpbmUgYXQgZW5kIG9mIGZpbGUu";
  gitWithDotGit = git // {
    path = "sources/hello-world-repo";
    git = git.git // {
      leaveDotGit = true;
      commitObject = {
        object = commitObject;
        gitlinks = [ ];
      };
    };
  };

  # A repository with one submodule, nix-test-repo-submodule, which the
  # tests of nixpkgs' fetchgit use. Its commit object and gitlinks come
  # from `git cat-file commit <rev> | base64 -w0` and `git ls-tree -r <rev>`.
  submoduleRepo = submodules: {
    path = if submodules then "sources/with-submodule" else "sources/without-submodule";
    git = {
      url = "https://github.com/pineapplehunter/nix-test-repo-with-submodule";
      rev = "26473335b84ead88ee0a3b649b1c7fa4a91cfd4a";
      inherit submodules;
      leaveDotGit = true;
      commitObject = {
        object = "dHJlZSA3YmJjMzE5N2U3MTRiNzFhZDg2M2UxN2YwZTRiNDVhZTU5MmM1NTZlCnBhcmVudCAyNGI3MzhhNDY5MDY0MGZhM2NkMzRhNWEwZTNkYTNmZTc2MGFjZTZhCmF1dGhvciBTaG9nbyBUYWthdGEgPHBlc2hvZ29AZ21haWwuY29tPiAxNjk0NDc4MTMxICswOTAwCmNvbW1pdHRlciBTaG9nbyBUYWthdGEgPHBlc2hvZ29AZ21haWwuY29tPiAxNjk0NDc4MTMxICswOTAwCgphZGQgY29udGVudAo=";
        gitlinks = [
          {
            path = "nix-test-repo-submodule";
            rev = "3223dfc5e18f5c15e0cb26ea7bdf5f12d215619b";
          }
        ];
      };
    };
    hash =
      if submodules then
        "sha256-rmP8PQT0wJBopdtr/hsB7Y/L1G+ZPdHC2r9LB05Qrj4="
      else
        "sha256-nmRXWeZpqvH69+rSbXZVlluXSN3FODeQyxL/BLVHuy8=";
  };

  # The checks of a rebuilt repository: HEAD is the locked commit, git
  # finds nothing missing, and `git status` finds no change.
  checkRepo = repo: rev: ''
    repo=${repo}
    git config --global --add safe.directory "$repo"
    test "$(git -C "$repo" rev-parse HEAD)" = ${rev}
    test "$(git -C "$repo" symbolic-ref HEAD)" = refs/heads/main
    test "$(cat "$repo/.git/shallow")" = ${rev}
    git -C "$repo" fsck --no-dangling
    git -C "$repo" --no-pager log --oneline
    status=$(git -C "$repo" status --porcelain)
    if [ -n "$status" ]; then
      echo "git status in $repo:" >&2
      echo "$status" >&2
      exit 1
    fi
  '';

  # A checkout of a different tree, which a replacement fetchgit serves in
  # place of the fetch.
  otherCheckout = runCommand "other-checkout" { } ''
    mkdir "$out"
    echo other > "$out/README"
  '';

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
    gitWithDotGit
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

  # A file fetched with Nix's built-in fetcher, and the manifest that names
  # it: the manifest maps the entry's path to the same store path.
  fetch-file-without-pkgs = runCommand "purifier-test-fetch-file-without-pkgs" { } ''
    fetched=${purifier.fetchEntry { } file}
    grep -q "Hello World" "$fetched"
    manifest=${
      purifier.manifest {
        name = "manifest.json";
        entries = [ file ];
      }
    }
    test "$(sed "s|$fetched|@fetched@|" "$manifest")" = ${
      lib.escapeShellArg (builtins.toJSON { ${file.path} = "@fetched@"; })
    }
    touch "$out"
  '';

  fetch-git = runCommand "purifier-test-fetch-git" { } ''
    src=${purifier.fetchEntry { inherit pkgs; } git}
    grep -q "Hello World" "$src/README"
    test ! -e "$src/.git"
    touch "$out"
  '';

  # The rebuilt repository: HEAD is the locked commit, its tree holds the
  # files of the checkout, and git finds nothing missing.
  fetch-git-dot-git =
    runCommand "purifier-test-fetch-git-dot-git" { nativeBuildInputs = [ pkgs.git ]; }
      ''
        export HOME="$TMPDIR"
        ${checkRepo (purifier.fetchEntry { inherit pkgs; } gitWithDotGit) git.git.rev}
        test "$(git -C "$repo" ls-tree -r --name-only HEAD)" = README
        touch "$out"
      '';

  # A repository with a submodule, with and without the submodule's
  # checkout: the index and the tree hold a gitlink at its path.
  fetch-git-dot-git-submodule =
    runCommand "purifier-test-fetch-git-dot-git-submodule" { nativeBuildInputs = [ pkgs.git ]; }
      ''
        export HOME="$TMPDIR"
        ${checkRepo (purifier.fetchEntry { inherit pkgs; } (submoduleRepo true))
          (submoduleRepo true).git.rev
        }
        test -n "$(ls -A "$repo/nix-test-repo-submodule")"
        git -C "$repo" ls-files -s nix-test-repo-submodule | grep -q "^160000 3223dfc5e18f5c15e0cb26ea7bdf5f12d215619b 0"
        ${checkRepo (purifier.fetchEntry { inherit pkgs; } (submoduleRepo false))
          (submoduleRepo false).git.rev
        }
        test -z "$(ls -A "$repo/nix-test-repo-submodule")"
        git -C "$repo" ls-files -s nix-test-repo-submodule | grep -q "^160000 3223dfc5e18f5c15e0cb26ea7bdf5f12d215619b 0"
        touch "$out"
      '';

  # A gitlink with another commit than the tree records fails the build.
  fetch-git-wrong-gitlink = pkgs.testers.testBuildFailure (
    purifier.fetchEntry { inherit pkgs; } (
      lib.recursiveUpdate (submoduleRepo false) {
        git.commitObject.gitlinks = [
          {
            path = "nix-test-repo-submodule";
            rev = "0000000000000000000000000000000000000000";
          }
        ];
      }
    )
  );

  # A commit object that is not the commit of `rev` fails the build.
  fetch-git-wrong-commit = pkgs.testers.testBuildFailure (
    purifier.fetchEntry { inherit pkgs; } (
      gitWithDotGit
      // {
        git = gitWithDotGit.git // {
          commitObject = gitWithDotGit.git.commitObject // {
            object = builtins.replaceStrings [ "TmV3" ] [ "T2xk" ] commitObject;
          };
        };
      }
    )
  );

  # A checkout whose tree is not the tree of the commit fails the build.
  fetch-git-wrong-tree = pkgs.testers.testBuildFailure (
    purifier.fetchEntry {
      inherit pkgs;
      fetchgit = _: otherCheckout;
    } gitWithDotGit
  );

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
    for p in files/README sources/hello-world sources/hello-world-repo sources/hello sources/hello-unstripped files/note.txt; do
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
