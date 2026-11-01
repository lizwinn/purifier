# SPDX-License-Identifier: MIT
#
# Tests that need no build: the check of entries and their paths, the
# arguments each kind gives its fetcher, `admits`, `wheels` and
# `wheelName`. The check fails with the names of the tests that fail.
{ pkgs, purifier }:
let
  inherit (pkgs) lib;
  entries = import ../nix/lib/entries.nix { inherit lib; };

  # A fetcher that returns the arguments it was given.
  echo = args: args;
  echoFetchers = {
    inherit pkgs;
    fetchurl = echo;
    fetchgit = echo;
    fetchhg = echo;
    fetchsvn = echo;
    fetchfossil = echo;
    fetchbzr = echo;
    fetchdarcs = echo;
    fetchpijul = echo;
    fetchcvs = echo;
  };
  fetchArgs = purifier.fetchEntry echoFetchers;

  hash = lib.fakeHash;
  file = {
    path = "files/a.txt";
    url = "https://example.org/a.txt";
    mirrors = [ "https://mirror.example.org/a.txt" ];
    inherit hash;
  };
  git = {
    path = "src/repo";
    git = {
      url = "https://example.org/repo.git";
      rev = "0123456789abcdef0123456789abcdef01234567";
      submodules = true;
      leaveDotGit = false;
      commitObject = null;
    };
    inherit hash;
  };
  cvs = {
    path = "src/cvs";
    cvs = {
      cvsRoot = ":pserver:anonymous@cvs.example.org:/cvsroot";
      module = "mod";
      tag = "release-1";
      date = null;
    };
    inherit hash;
  };

  commitObjectSet = {
    object = "dHJlZQ==";
    gitlinks = [
      {
        path = "vendor/lib";
        rev = "3223dfc5e18f5c15e0cb26ea7bdf5f12d215619b";
      }
    ];
  };

  # A git entry with a .git directory, and a commitObject with `changes`.
  dotGitWith =
    changes:
    git
    // {
      git = git.git // {
        leaveDotGit = true;
        commitObject = commitObjectSet // changes;
      };
    };

  darcs = {
    url = "https://example.org/darcs";
    rev = null;
    context = "Context:\n\n[patch\nauthor**20200101000000] \n";
  };
  darcsContext = {
    path = "d";
    inherit darcs hash;
  };

  # Whether the problem of an entry names its path and has `text` in it.
  problemSays =
    entry: text:
    let
      p = entries.problem entry;
    in
    p != null && lib.hasInfix (builtins.toJSON entry.path) p && lib.hasInfix text p;

  fails = value: !(builtins.tryEval (builtins.deepSeq value value)).success;

  admitsFilters =
    filters: system:
    purifier.admits (
      {
        os = null;
        cpu = null;
        libc = null;
      }
      // filters
    ) system;

  # The wheel cases of uv-purifier's tests of wheels.nix.
  installs = purifier.wheels {
    pythonVersion = "3.14";
    arch = "x86_64";
    glibcVersion = "2.40";
  };

  # The cases of poetry-purifier's tests of wheel-name.nix.
  wheelName = purifier.wheelName {
    pythonVersion = "3.13";
    arch = "aarch64";
    glibcVersion = "2.40";
  };

  tests = {
    # Entries of each kind pass the check.
    testValidKinds = {
      expr = map entries.problem [
        file
        git
        cvs
        {
          path = "inline";
          contents = "text";
        }
        {
          path = "a";
          archive = {
            url = "https://example.org/a.tar.gz";
            mirrors = [ ];
            stripTop = true;
          };
          inherit hash;
        }
        {
          path = "h";
          hg = {
            url = "https://example.org/hg";
            rev = "abc";
            subrepos = false;
          };
          inherit hash;
        }
        {
          path = "s";
          svn = {
            url = "https://example.org/svn";
            rev = 12;
            ignoreExternals = false;
            ignoreKeywords = false;
          };
          inherit hash;
        }
        {
          path = "f";
          fossil = {
            url = "https://example.org/fossil";
            rev = "abc";
          };
          inherit hash;
        }
        {
          path = "b";
          bzr = {
            url = "https://example.org/bzr";
            rev = "3";
          };
          inherit hash;
        }
        {
          path = "d";
          darcs = {
            url = "https://example.org/darcs";
            rev = "abc";
            context = null;
          };
          inherit hash;
        }
      ];
      expected = lib.genList (_: null) 10;
    };

    # Other top-level fields are left alone.
    testExtraFields = {
      expr = entries.problem (file // { os = [ "linux" ]; });
      expected = null;
    };

    testKinds = {
      expr = map entries.kindOf [
        file
        git
        cvs
      ];
      expected = [
        "file"
        "git"
        "cvs"
      ];
    };

    testNoKind = {
      expr = problemSays { path = "x"; } "no kind key";
      expected = true;
    };
    testUnknownKind = {
      expr = problemSays {
        path = "x";
        perforce = { };
        inherit hash;
      } "no kind key";
      expected = true;
    };
    testTwoKinds = {
      expr = problemSays (file // { inherit (git) git; }) "`url`, `git`";
      expected = true;
    };
    testMissingField = {
      expr = problemSays (removeAttrs file [ "mirrors" ]) "no `mirrors`";
      expected = true;
    };
    testMissingHash = {
      expr = problemSays (removeAttrs git [ "hash" ]) "no `hash`";
      expected = true;
    };
    testMissingSubField = {
      expr = problemSays (git // { git = removeAttrs git.git [ "leaveDotGit" ]; }) "no `git.leaveDotGit`";
      expected = true;
    };
    testGitDotGit = {
      expr = entries.problem (
        git
        // {
          git = git.git // {
            submodules = false;
            leaveDotGit = true;
            commitObject = commitObjectSet;
          };
        }
      );
      expected = null;
    };
    testGitDotGitWithoutCommit = {
      expr = problemSays (
        git
        // {
          git = git.git // {
            submodules = false;
            leaveDotGit = true;
          };
        }
      ) "`git.commitObject` is null";
      expected = true;
    };
    testGitCommitWithoutDotGit = {
      expr = problemSays (
        git
        // {
          git = git.git // {
            commitObject = commitObjectSet;
          };
        }
      ) "is not null";
      expected = true;
    };
    # A checkout with submodules and a .git directory.
    testGitDotGitWithSubmodules = {
      expr = entries.problem (dotGitWith { });
      expected = null;
    };
    testGitCommitObjectString = {
      expr = problemSays (
        dotGitWith { }
        // {
          git = (dotGitWith { }).git // {
            commitObject = "dHJlZQ==";
          };
        }
      ) "is not null or an attribute set";
      expected = true;
    };
    testGitCommitObjectNoObject = {
      expr = problemSays (
        git
        // {
          git = git.git // {
            leaveDotGit = true;
            commitObject.gitlinks = [ ];
          };
        }
      ) "no `git.commitObject.object`";
      expected = true;
    };
    testGitCommitObjectNoGitlinks = {
      expr = problemSays (
        git
        // {
          git = git.git // {
            leaveDotGit = true;
            commitObject.object = "dHJlZQ==";
          };
        }
      ) "no `git.commitObject.gitlinks`";
      expected = true;
    };
    testGitlinkShortRev = {
      expr = problemSays (dotGitWith {
        gitlinks = [
          {
            path = "vendor/lib";
            rev = "3223dfc";
          }
        ];
      }) "`git.commitObject.gitlinks.0.rev` is not a SHA-1";
      expected = true;
    };
    testGitlinkAbsolutePath = {
      expr = problemSays (dotGitWith {
        gitlinks = [
          {
            path = "/vendor/lib";
            rev = "3223dfc5e18f5c15e0cb26ea7bdf5f12d215619b";
          }
        ];
      }) "`git.commitObject.gitlinks.0.path` is absolute";
      expected = true;
    };
    testGitlinkNoRev = {
      expr = problemSays (dotGitWith {
        gitlinks = [ { path = "vendor/lib"; } ];
      }) "no `git.commitObject.gitlinks.0.rev`";
      expected = true;
    };
    testGitlinkTwice = {
      expr = problemSays (dotGitWith {
        gitlinks = commitObjectSet.gitlinks ++ commitObjectSet.gitlinks;
      }) "more than once";
      expected = true;
    };
    testGitMissingCommitObject = {
      expr = problemSays (
        git // { git = removeAttrs git.git [ "commitObject" ]; }
      ) "no `git.commitObject`";
      expected = true;
    };
    testMistypedField = {
      expr = problemSays (
        git
        // {
          git = git.git // {
            submodules = "yes";
          };
        }
      ) "`git.submodules` is not a boolean";
      expected = true;
    };
    testMistypedMirrors = {
      expr = problemSays (
        file // { mirrors = "https://example.org"; }
      ) "`mirrors` is not a list of strings";
      expected = true;
    };
    testCvsBoth = {
      expr = problemSays (
        cvs
        // {
          cvs = cvs.cvs // {
            date = "2020-01-01";
          };
        }
      ) "exactly one of";
      expected = true;
    };
    testCvsNeither = {
      expr = problemSays (
        cvs
        // {
          cvs = cvs.cvs // {
            tag = null;
          };
        }
      ) "exactly one of";
      expected = true;
    };
    testCvsDate = {
      expr = entries.problem (
        cvs
        // {
          cvs = cvs.cvs // {
            tag = null;
            date = "2020-01-01";
          };
        }
      );
      expected = null;
    };
    testNoPath = {
      expr = lib.hasInfix "no `path`" (entries.problem (removeAttrs file [ "path" ]));
      expected = true;
    };

    # Paths are relative and have no `..`, `.` or empty components.
    testAbsolutePath = {
      expr = problemSays (file // { path = "/etc/passwd"; }) "absolute";
      expected = true;
    };
    testDotDotPath = {
      expr = problemSays (file // { path = "a/../../b"; }) "`..`";
      expected = true;
    };
    testLeadingDotDotPath = {
      expr = problemSays (file // { path = ".."; }) "`..`";
      expected = true;
    };
    testDotPath = {
      expr = problemSays (file // { path = "./a"; }) "`.`";
      expected = true;
    };
    testEmptyComponentPath = {
      expr = problemSays (file // { path = "a//b"; }) "empty component";
      expected = true;
    };
    testTrailingSlashPath = {
      expr = problemSays (file // { path = "a/"; }) "empty component";
      expected = true;
    };
    testEmptyPath = {
      expr = problemSays (file // { path = ""; }) "empty";
      expected = true;
    };
    testDottedNamesPath = {
      expr = entries.problem (file // { path = "..a/.b/c..d"; });
      expected = null;
    };

    # fetchEntry fails evaluation on an entry with a problem.
    testFetchEntryChecks = {
      expr = fails (fetchArgs (removeAttrs file [ "hash" ]));
      expected = true;
    };

    # The arguments each kind gives its fetcher.
    testFileArgs = {
      expr = fetchArgs file;
      expected = {
        name = "a.txt";
        urls = [
          "https://example.org/a.txt"
          "https://mirror.example.org/a.txt"
        ];
        inherit hash;
      };
    };
    testGitArgs = {
      expr = fetchArgs git;
      expected = {
        name = "repo";
        inherit (git.git) url rev;
        fetchSubmodules = true;
        leaveDotGit = false;
        inherit hash;
      };
    };
    testHgArgs = {
      expr = fetchArgs {
        path = "h";
        hg = {
          url = "https://example.org/hg";
          rev = "abc";
          subrepos = true;
        };
        inherit hash;
      };
      expected = {
        name = "h";
        url = "https://example.org/hg";
        rev = "abc";
        fetchSubrepos = true;
        inherit hash;
      };
    };
    testSvnArgs = {
      expr = fetchArgs {
        path = "s";
        svn = {
          url = "https://example.org/svn";
          rev = 12;
          ignoreExternals = true;
          ignoreKeywords = false;
        };
        inherit hash;
      };
      expected = {
        name = "s";
        url = "https://example.org/svn";
        rev = 12;
        ignoreExternals = true;
        ignoreKeywords = false;
        inherit hash;
      };
    };
    testFossilArgs = {
      expr = fetchArgs {
        path = "f";
        fossil = {
          url = "https://example.org/fossil";
          rev = "abc";
        };
        inherit hash;
      };
      expected = {
        name = "f";
        url = "https://example.org/fossil";
        rev = "abc";
        inherit hash;
      };
    };
    testBzrArgs = {
      expr = fetchArgs {
        path = "b";
        bzr = {
          url = "https://example.org/bzr";
          rev = "3";
        };
        inherit hash;
      };
      expected = {
        url = "https://example.org/bzr";
        rev = "3";
        inherit hash;
      };
    };
    testDarcsArgs = {
      expr = fetchArgs {
        path = "d";
        darcs = {
          url = "https://example.org/darcs";
          rev = "abc";
          context = null;
        };
        inherit hash;
      };
      expected = {
        name = "d";
        url = "https://example.org/darcs";
        rev = "abc";
        inherit hash;
      };
    };
    testPijulArgs = {
      expr = fetchArgs {
        path = "p";
        pijul = {
          url = "https://example.org/pijul";
          rev = "STATE";
        };
        inherit hash;
      };
      expected = {
        name = "p";
        url = "https://example.org/pijul";
        state = "STATE";
        inherit hash;
      };
    };
    testPijulMissingRev = {
      expr = problemSays {
        path = "p";
        pijul.url = "https://example.org/pijul";
        inherit hash;
      } "no `pijul.rev`";
      expected = true;
    };
    testCvsArgs = {
      expr = fetchArgs cvs;
      expected = {
        inherit (cvs.cvs) cvsRoot module tag;
        inherit hash;
      };
    };
    testDarcsContextArgs = {
      expr =
        let
          args = fetchArgs darcsContext;
        in
        {
          inherit (args) name url hash;
          hasRev = args ? rev;
          context = args.context.text;
          contextName = args.context.name;
        };
      expected = {
        name = "d";
        url = "https://example.org/darcs";
        inherit hash;
        hasRev = false;
        context = "Context:\n\n[patch\nauthor**20200101000000] \n";
        contextName = "d-context";
      };
    };
    testDarcsContextValid = {
      expr = entries.problem darcsContext;
      expected = null;
    };
    testDarcsBoth = {
      expr = problemSays (
        darcsContext
        // {
          darcs = darcsContext.darcs // {
            rev = "tag";
          };
        }
      ) "exactly one of";
      expected = true;
    };
    testDarcsNeither = {
      expr = problemSays (
        darcsContext
        // {
          darcs = darcsContext.darcs // {
            context = null;
          };
        }
      ) "exactly one of";
      expected = true;
    };
    testDarcsMissingContext = {
      expr = problemSays (
        darcsContext // { darcs = removeAttrs darcsContext.darcs [ "context" ]; }
      ) "no `darcs.context`";
      expected = true;
    };
    testSvnMissingIgnoreKeywords = {
      expr = problemSays {
        path = "s";
        svn = {
          url = "https://example.org/svn";
          rev = 12;
          ignoreExternals = false;
        };
        inherit hash;
      } "no `svn.ignoreKeywords`";
      expected = true;
    };
    testInline = {
      expr = builtins.readFile (fetchArgs {
        path = "dir/note.txt";
        contents = "hello\n";
      });
      expected = "hello\n";
    };
    testArchiveFetchArgs = {
      expr =
        (fetchArgs {
          path = "a";
          archive = {
            url = "https://example.org/dl/a-1.0.tar.gz?download=1";
            mirrors = [ "https://mirror.example.org/a-1.0.tar.gz" ];
            stripTop = false;
          };
          inherit hash;
        }).src;
      expected = {
        name = "a-1.0.tar.gz";
        urls = [
          "https://example.org/dl/a-1.0.tar.gz?download=1"
          "https://mirror.example.org/a-1.0.tar.gz"
        ];
        inherit hash;
      };
    };

    # tree fails evaluation on entries at the same path, an entry inside
    # another, and an entry with a problem.
    testTreeDuplicate = {
      expr =
        fails
          (purifier.tree {
            inherit pkgs;
            name = "t";
            entries = [
              file
              (file // { url = "https://example.org/b.txt"; })
            ];
          }).drvPath;
      expected = true;
    };
    testTreeNested = {
      expr =
        fails
          (purifier.tree {
            inherit pkgs;
            name = "t";
            entries = [
              git
              (file // { path = "src/repo/extra.txt"; })
            ];
          }).drvPath;
      expected = true;
    };
    testTreeSiblings = {
      expr =
        fails
          (purifier.tree {
            inherit pkgs;
            name = "t";
            entries = [
              (file // { path = "src/a"; })
              (file // { path = "src/a-b"; })
              (file // { path = "src/a.b/c"; })
            ];
          }).drvPath;
      expected = false;
    };
    testTreeChecksEntries = {
      expr =
        fails
          (purifier.tree {
            inherit pkgs;
            name = "t";
            entries = [ (file // { path = "../escape"; }) ];
          }).drvPath;
      expected = true;
    };

    # admits, with npm's meanings.
    testAdmitsNoFilters = {
      expr = map (admitsFilters { }) [
        "x86_64-linux"
        "aarch64-linux"
      ];
      expected = [
        true
        true
      ];
    };
    testAdmitsString = {
      expr = map (admitsFilters { cpu = "x64"; }) [
        "x86_64-linux"
        "aarch64-linux"
      ];
      expected = [
        true
        false
      ];
    };
    testAdmitsList = {
      expr =
        map
          (admitsFilters {
            cpu = [
              "arm64"
              "ia32"
            ];
          })
          [
            "x86_64-linux"
            "aarch64-linux"
          ];
      expected = [
        false
        true
      ];
    };
    testAdmitsNegated = {
      expr = map (admitsFilters { cpu = [ "!arm64" ]; }) [
        "x86_64-linux"
        "aarch64-linux"
      ];
      expected = [
        true
        false
      ];
    };
    testAdmitsNegatedWins = {
      expr = admitsFilters {
        cpu = [
          "x64"
          "!x64"
        ];
      } "x86_64-linux";
      expected = false;
    };
    testAdmitsMixed = {
      expr = admitsFilters {
        os = [
          "darwin"
          "!win32"
        ];
      } "x86_64-linux";
      expected = false;
    };
    testAdmitsEmptyList = {
      expr = admitsFilters { os = [ ]; } "x86_64-linux";
      expected = true;
    };
    testAdmitsAny = {
      expr = admitsFilters { os = "any"; } "aarch64-linux";
      expected = true;
    };
    testAdmitsOtherOs = {
      expr = admitsFilters {
        os = [
          "darwin"
          "win32"
        ];
      } "x86_64-linux";
      expected = false;
    };
    testAdmitsLibc = {
      expr = map (libc: admitsFilters { inherit libc; } "x86_64-linux") [
        "glibc"
        "musl"
        [ "!musl" ]
      ];
      expected = [
        true
        false
        true
      ];
    };
    testAdmitsAllFields = {
      expr = purifier.admits {
        os = "linux";
        cpu = "arm64";
        libc = "glibc";
      } "aarch64-linux";
      expected = true;
    };
    testAdmitsUnknownSystem = {
      expr = fails (admitsFilters { } "x86_64-darwin");
      expected = true;
    };
    testAdmitsBadFilter = {
      expr = fails (admitsFilters { os = 1; } "x86_64-linux");
      expected = true;
    };

    testWheels = {
      expr = map installs [
        "attrs-25.3.0-py3-none-any.whl"
        "six-1.17.0-py2.py3-none-any.whl"
        "pydantic_core-2.41.4-cp314-cp314-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
        "pydantic_core-2.41.4-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
        "cryptography-46.0.3-cp311-abi3-manylinux_2_34_x86_64.whl"
        "cryptography-46.0.3-cp315-abi3-manylinux_2_34_x86_64.whl"
        "numpy-2.3.4-cp314-cp314-manylinux_2_41_x86_64.whl"
        "numpy-2.3.4-cp314-cp314-manylinux_2_28_aarch64.whl"
        "numpy-2.3.4-cp314-cp314-musllinux_1_2_x86_64.whl"
        "numpy-2.3.4-cp314-cp314-macosx_14_0_arm64.whl"
        "example-1.0-1-py3-none-any.whl"
        "example-1.0-py2-none-any.whl"
      ];
      expected = [
        true
        true
        true
        false
        true
        false
        false
        false
        false
        false
        true
        false
      ];
    };

    testWheelName = {
      expr = map wheelName [
        "app-1.0.0-py3-none-any.whl"
        "fast-1.0-cp314-cp314-linux_x86_64.whl"
        "fast-1.0-cp314-cp314-manylinux_2_44_x86_64.whl"
        "fast-1.0-cp311-abi3-linux_x86_64.whl"
      ];
      expected = [
        "app-1.0.0-py3-none-any.whl"
        "fast-1.0-cp313-cp313-linux_aarch64.whl"
        "fast-1.0-cp313-cp313-manylinux_2_40_aarch64.whl"
        "fast-1.0-cp311-abi3-linux_aarch64.whl"
      ];
    };
    testWheelNameOtherPlatform = {
      expr = fails (wheelName "fast-1.0-cp314-cp314-macosx_14_0_arm64.whl");
      expected = true;
    };

    # Without `pkgs`, a file entry is fetched with Nix's built-in fetcher,
    # at the store path that nixpkgs' fetchurl gives it.
    testFetchFileWithoutPkgs = {
      expr = (purifier.fetchEntry { } file).outPath;
      expected = (purifier.fetchEntry { inherit pkgs; } file).outPath;
    };
    testFetchInlineWithoutPkgs = {
      expr = purifier.fetchEntry { } {
        path = "files/note.txt";
        contents = "a note\n";
      };
      expected = builtins.toFile "note.txt" "a note\n";
    };
    # The default fetchcvs exports CVSREADONLYFS to cvs. Under structured
    # attributes only the variables under `env` reach the builder.
    testFetchCvsReadOnly = {
      expr = (purifier.fetchEntry { inherit pkgs; } cvs).drvAttrs.env.CVSREADONLYFS or null;
      expected = "1";
    };
    # Without `pkgs`, a fetcher the caller passes is used, and the steps
    # that build with nixpkgs fail.
    testFetchGitWithoutPkgsTakesFetcher = {
      expr = (purifier.fetchEntry { fetchgit = echo; } git).url;
      expected = git.git.url;
    };
    testFetchDotGitWithoutPkgs = {
      expr = fails (purifier.fetchEntry { fetchgit = echo; } (dotGitWith { }));
      expected = true;
    };
    testFetchHgWithoutPkgsOrFetcher = {
      expr = fails (
        purifier.fetchEntry { } {
          path = "src/hg";
          hg = {
            url = "https://example.org/hg";
            rev = "0123456789abcdef0123456789abcdef01234567";
            subrepos = false;
          };
          inherit hash;
        }
      );
      expected = true;
    };

    # The manifest's text refers to the store paths it names, and
    # fromJSON refuses such a string, so the test compares the text.
    testManifestInline = {
      expr = builtins.readFile (
        purifier.manifest {
          name = "manifest.json";
          entries = [
            {
              path = "files/note.txt";
              contents = "a note\n";
            }
          ];
        }
      );
      expected = builtins.toJSON {
        "files/note.txt" = builtins.toFile "note.txt" "a note\n";
      };
    };
    testManifestRefusesGit = {
      expr = fails (
        purifier.manifest {
          name = "manifest.json";
          entries = [ git ];
        }
      );
      expected = true;
    };

    # The root default.nix is the library outside a flake.
    testRootEntryPoint = {
      expr = builtins.attrNames (import ../default.nix { inherit lib; });
      expected = builtins.attrNames purifier;
    };
  };

  failures = lib.debug.runTests tests;
in
pkgs.runCommand "purifier-test-evaluation" { } (
  if failures == [ ] then
    "touch $out"
  else
    ''
      echo ${lib.escapeShellArg (builtins.toJSON failures)} >&2
      exit 1
    ''
)
