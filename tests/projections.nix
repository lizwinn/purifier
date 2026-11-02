# SPDX-License-Identifier: MIT
{ pkgs, purifier }:
let
  source = pkgs.runCommand "projection-source" { } "mkdir -p $out";
  other = pkgs.runCommand "projection-other-source" { } "mkdir -p $out; touch $out/other";
  maven = pkgs.runCommand "maven-view" { } "mkdir -p $out; ln -s ${source} $out/source";
  ivy = pkgs.runCommand "ivy-view" { } "mkdir -p $out; ln -s ${source} $out/source";
  producer = source // {
    projections = { inherit maven ivy; };
  };
  select =
    formats:
    purifier.projectInputs {
      dependencies = [ producer ];
      inherit formats;
    };
  entry = {
    path = "org/example/library.jar";
    contents = "same artifact";
  };
  repositoryTree =
    link:
    purifier.tree {
      inherit pkgs link;
      name = "projection-provenance-${if link then "linked" else "copied"}";
      entries = [ entry ];
    };
  linked = repositoryTree true;
  copied = repositoryTree false;
  identity = purifier.inputIdentity source;
  projectionHash =
    repository: dependency:
    let
      projection = purifier.jvmProjections {
        inherit repository;
        dependencies = [ dependency ];
        inherit (pkgs) runCommand python3;
        name = "projection-identity-test";
      };
    in
    (builtins.fromJSON (builtins.unsafeDiscardStringContext projection.maven.spec)).producerHash;
  supplied =
    view:
    source
    // {
      projections.maven = view // {
        catalog = "${view}/catalog.json";
      };
    };
  force = value: builtins.tryEval (builtins.deepSeq value true);
in
# No import from a derivation: all three identities are evaluated while the
# synthetic repositories have not been built and contain no Maven catalog.
assert projectionHash source (supplied maven) == projectionHash source (supplied maven);
assert projectionHash source (supplied maven) != projectionHash source (supplied ivy);
assert projectionHash source (supplied maven) != projectionHash other (supplied maven);
assert !(force (projectionHash "/home/user/repository" (supplied maven))).success;
assert identity.hash == (purifier.inputIdentity source).hash;
assert identity.hash != (purifier.inputIdentity other).hash;
assert builtins.attrNames identity.context == [ source.drvPath ];
assert
  (builtins.head (select [
    "maven"
    "ivy"
  ])).format == "maven";
assert (builtins.head (select [ "ivy" ])).repository == ivy;
assert
  (builtins.head (select [ "maven" ])).identity.hash == (builtins.head (select [ "ivy" ]))
  .identity.hash;
assert
  (builtins.head (select [ "maven" ])).repositoryIdentity.hash != (builtins.head (select [ "ivy" ]))
  .repositoryIdentity.hash;
assert !(force (select [ "npm" ])).success;
assert !(force (purifier.inputIdentity "/home/user/.m2/repository")).success;
assert
  !(force (
    purifier.projectInputs {
      dependencies = [ producer ];
      formats = [ ];
    }
  )).success;
assert builtins.attrNames copied.provenance == [ entry.path ];
assert
  (purifier.inputIdentity copied.provenance.${entry.path}).hash
  == (purifier.inputIdentity linked.provenance.${entry.path}).hash;
pkgs.runCommand "purifier-projection-evaluation" { } ''
  cmp ${copied}/${entry.path} ${linked}/${entry.path}
  test "$(find ${copied} -type f | wc -l)" = 1
  test ! -L ${copied}/${entry.path}
  test -L ${linked}/${entry.path}
  touch $out
''
