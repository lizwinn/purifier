# purifier

purifier is the Nix library that the nix-purifiers share. Each purifier
writes a lockfile that lists the downloads a build needs, as download
entries. This library turns each entry into a store path, and lays a list
of entries out as one directory tree. It also holds the platform and wheel
helpers that more than one purifier needs.

The nix-purifiers family targets x86_64-linux and aarch64-linux, and
this library also supports aarch64-darwin. Every purifier meets the
contract in [CONTRACT.md](CONTRACT.md).

## Use

The flake's `lib` output is the whole library, and the functions are under
`lib.purifier`:

```nix
{
  inputs.purifier.url = "github:nix-purifiers/purifier";
  inputs.nixpkgs.follows = "purifier/nixpkgs";

  outputs = { nixpkgs, purifier, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      inherit (purifier.lib.purifier) tree;
    in
    {
      packages.${system}.downloads = tree {
        inherit pkgs;
        name = "downloads";
        entries = (builtins.fromJSON (builtins.readFile ./my.lock.json)).downloads;
      };
    };
}
```

A flake on the caisson layout lists `purifier` in its `projects`, and its
library then has `lib.purifier`.

An evaluation outside a flake, such as the `nix-build` that a purifier's
command runs, imports the root `default.nix` of the library's source with
a nixpkgs library. The result is the attribute set that the flake has as
`lib.purifier`:

```nix
purifier = import "${purifierSource}" { inherit lib; };
```

A purifier takes `purifierSource` from its own flake.lock, so its command
and its flake use the same revision of the library. We recommend
importing only the root `default.nix`, because the layout of the files
under `nix/` can change.

## Entries

An entry is an attribute set. Its kind is decided by which kind key it has,
and an entry has exactly one kind key. Every field of its kind is required,
and no field has a default. An entry may carry other top-level fields, such
as the `os`, `cpu` and `libc` filters of npm, and the library leaves them
alone.

`path` is where the entry lies in a tree. It is relative to the root of
the tree. A path that is empty or absolute fails evaluation, and so does a
path with a `..`, a `.` or an empty component. A doubled or a trailing `/`
makes an empty component.

`hash` is an SRI hash, of the kind that nixpkgs' fetchers take.

| Kind | Kind key | Shape |
| --- | --- | --- |
| file | `url` | `{ path, url, mirrors, hash }` |
| inline | `contents` | `{ path, contents }` |
| git | `git` | `{ path, git = { url, rev, submodules, leaveDotGit, commitObject }, hash }` |
| archive | `archive` | `{ path, archive = { url, mirrors, stripTop }, hash }` |
| hg | `hg` | `{ path, hg = { url, rev, subrepos }, hash }` |
| svn | `svn` | `{ path, svn = { url, rev, ignoreExternals, ignoreKeywords }, hash }` |
| fossil | `fossil` | `{ path, fossil = { url, rev }, hash }` |
| bzr | `bzr` | `{ path, bzr = { url, rev }, hash }` |
| darcs | `darcs` | `{ path, darcs = { url, rev, context }, hash }` |
| pijul | `pijul` | `{ path, pijul = { url, rev }, hash }` |
| cvs | `cvs` | `{ path, cvs = { cvsRoot, module, tag, date }, hash }` |

- **file** is a file, fetched with `fetchurl` from `url` and then from
  each of `mirrors`, a list of URLs that serve the same file. `hash` is the
  hash of the file.
- **inline** is a file whose text is `contents`, made with
  `builtins.toFile`. It needs no download.
- **git** is a checkout at the commit `rev`, fetched with nixpkgs'
  `fetchgit` without its `.git` directory. The checkout has its submodules
  when `submodules` is true. `hash` is the hash that `nix-prefetch-git`
  reports for the checkout without `--leave-dotGit`.

  When `leaveDotGit` is false, `commitObject` is null. When `leaveDotGit`
  is true, `commitObject` is `{ object, gitlinks }`. `object` is the raw
  commit object of `rev` in base64, as
  `git cat-file commit <rev> | base64 -w0` prints it. `gitlinks` lists
  each submodule of the commit's tree as `{ path, rev }`, from the lines of
  mode 160000 that `git ls-tree -r <rev>` prints, and it is empty for a
  commit with no submodules. Each gitlink `rev` is a full SHA-1, and each
  gitlink `path` follows the rules of an entry's `path`.

  The `.git` directory that git writes during a fetch differs from one
  fetch to the next, so the library rebuilds a shallow `.git` directory in
  a second derivation. That derivation writes the files and directories of
  the checkout as loose git objects. At each gitlink path it writes a
  gitlink with the recorded commit instead of the directory, whether the
  checkout holds the submodule there or an empty directory. It then writes
  the recorded commit object. The build fails unless the commit object
  hashes to `rev` and names the tree written from the checkout. `HEAD`
  names the branch `main`, which points at `rev`, and `.git/shallow` lists
  `rev` when the commit has parents. The index lists every file, link and
  gitlink of the tree with zero stat data, so `git status` compares file
  contents and finds no change.
- **archive** is the content of an archive file, as a directory. The file
  is fetched with `fetchurl` from `url` and then from each of `mirrors`,
  and `hash` is the hash of the file. A zip file is unpacked with `unzip`,
  and every other file with `tar`, which reads gzip, bzip2, xz and zstd
  compression. When `stripTop` is true, the archive must hold exactly one
  top-level directory, and that directory's content is the result; the
  build fails on any other archive. When `stripTop` is false, the result
  is everything at the top of the archive.
- **hg** is a Mercurial checkout at `rev`, fetched with `fetchhg`, with its
  subrepositories when `subrepos` is true.
- **svn** is a Subversion export at the revision `rev`, fetched with
  `fetchsvn`. `rev` is a string or an integer. The export leaves out
  externals when `ignoreExternals` is true, and leaves keywords unexpanded
  when `ignoreKeywords` is true.
- **fossil** is a Fossil checkout at `rev`, fetched with `fetchfossil`.
- **bzr** is a Bazaar export at the revision `rev`, fetched with
  `fetchbzr`.
- **darcs** is a Darcs checkout, fetched with `fetchdarcs`. Of `rev` and
  `context`, exactly one is a string and the other is null. `rev` is a
  tag. `context` is the text of a Darcs context file, which the library
  writes to the store and passes to `fetchdarcs` as its `context`.
- **pijul** is a Pijul checkout of the state `rev`, fetched with
  `fetchpijul`, which takes it as its `state`.
- **cvs** is a CVS export of `module` from the repository `cvsRoot`,
  fetched with `fetchcvs`. Of `tag` and `date`, exactly one is a string and
  the other is null.

Every kind but file and inline lies in a tree as a directory.

An entry with no kind key fails evaluation, and so do an entry with two
kind keys, a missing field and a field of the wrong type. The error message
names the path of the entry.

## Functions

### `fetchEntry`

```nix
fetchEntry { pkgs ? null, fetchurl ? pkgs.fetchurl, fetchgit ? pkgs.fetchgit, ... } entry
```

The store path of one entry. The first argument takes `pkgs`, and a
fetcher for each kind that downloads: `fetchurl`, `fetchgit`, `fetchhg`,
`fetchsvn`, `fetchfossil`, `fetchbzr`, `fetchdarcs`, `fetchpijul` and
`fetchcvs`. Each
fetcher is nixpkgs' own unless it is given. A purifier passes its own
fetcher in place of one, such as a `fetchurl` that sends credentials. A
replacement takes the arguments that this library passes to the nixpkgs
fetcher of the same name. fetchEntry sets `CVSREADONLYFS` on the `fetchcvs` it is given, so
cvs writes no lock files into the repository it exports from.

`pkgs` may be left out, for an evaluation without nixpkgs. A file entry is
then fetched with Nix's built-in fetcher, and its store path is the store
path that nixpkgs' `fetchurl` gives it. The built-in fetcher downloads
only from `url` and never tries `mirrors`, so we recommend passing `pkgs`
wherever an entry's `url` can fail and a mirror would serve it. So a sandboxed build with `pkgs`
and a command without nixpkgs share the fetched files. A git entry whose
`leaveDotGit` is false is fetched with `builtins.fetchGit`, which checks
the checkout against `hash`; its store path differs from the one that
nixpkgs' `fetchgit` gives. An inline entry needs no fetcher. Every other
kind needs `pkgs` or the fetcher of its kind. Unpacking an archive needs
`runCommand`, `unzip` and `zstd`, rebuilding a `.git` directory needs
`runCommand` and `python3`, and writing a Darcs context file needs
`writeText`. `fetchEntry` takes each of them by name, or from `pkgs`, so a
builder written for `callPackage` passes the packages it takes by name.
Evaluation fails when an entry needs one that it was not given.

The fetches are named after the last component of the entry's `path`.
The archive file of an archive entry is named after the last segment of
its URL.

### `tree`

```nix
tree { pkgs, name, entries, link ? true, fetchurl ? pkgs.fetchurl, ... }
```

One derivation, named `name`, in which each of `entries` lies at its
`path`. When `link` is true, each entry is a symbolic link into the store.
When `link` is false, each entry is a copy, for the tools that ignore files
behind symbolic links. `pkgs` and the fetchers go to `fetchEntry`.

Two entries at the same path fail evaluation, and so does an entry whose
path lies inside the path of another entry.

The result exposes `provenance`, an attribute set mapping each entry path
to its fetched Nix source. This preserves per-entry provenance when `link`
is false as well as when it is true. It adds no files to the tree.

### `inputIdentity` and `projectInputs`

`inputIdentity input` returns the input's Nix string `context` and a SHA-256
`hash` of that context. It inspects derivation/output references without
building outputs. Inputs without Nix context fail. This is a build-input
identity, not a content hash or a traversal of runtime references.

`projectInputs { dependencies; formats; }` selects the first supported
format from each dependency's `projections` attribute set. For example, a
consumer accepting `[ "maven" "ivy" ]` prefers `dependency.projections.maven`
and otherwise accepts `dependency.projections.ivy`. Missing formats fail.
Each result contains `producer`, `format`, `repository`, `identity` for the
producer, and `repositoryIdentity` for the selected view. Two formats of
one producer retain the same producer identity.

These primitives support format adapters; they do not themselves override
native version resolution or rewrite publication metadata.

### `lockedProjectInputs`

`lockedProjectInputs { inputs; system; }` resolves the Nix packages recorded
by native lock commands. Each entry contains `reference` (the user's flake
installable), Nix's content-pinned `url` and `narHash`, and its package
`attribute`. The source hash is checked before selecting a package.

Native Python integrations can import `project_inputs` from
`projectInputsTool`. `pin(references, previous, refresh=False)` preserves
existing Nix pins until an update; `maven_views(inputs)` realizes each
package's Maven projection and its catalog together. Consumers record these
Nix identities separately from artifact downloads and exclude provided
repository files from the download lock.

### `npmProjections`

`npmProjections { graph; archives; producer ? archives; roots ? null;
dependencies ? []; runCommand; python3; }` exports `projections.npm`.
The adapter supplies npm's actual runtime graph and the prepared package
archives. The result contains native npm tarballs and a catalog of names,
versions, integrity hashes and primary publications.

Versions encode Nix producer identities and the selected dependency graph.
Dependency and peer metadata names the exact exported versions, including
aliases. Foreign package identities follow their content and runtime graph,
so equivalent dependencies can be shared across producers. Supplied archives
retain their original bytes. Native build hooks have already run; exports
contain their results and do not rerun those hooks when installed.

Consumers select this format with `projectInputs` and can realize it through
`project_inputs.npm_views`. Only Nix source pins belong in the download lock
for supplied artifacts. Publish the tarballs through a native npm registry;
ordinary npm consumers need no purifier plugin to install them.

### `rubygemsProjections`

`rubygemsProjections { archives; producer; roots ? null; dependencies ? [];
ruby; runCommand; intrinsics ? []; }` exports `projections.rubygems` with a `catalog` attribute.
Pass native `.gem` archive paths and the Nix producer that owns them.
The adapter uses RubyGems to rebuild owned packages with Nix-derived versions
and exact runtime requirements for supplied gems. Dependency projections keep
their archive bytes and versions; their integrity and native coordinates are
validated before reuse. `roots` selects publications and their runtime closure.
Ordinary Bundler consumers can install the repository.

SDK-delivered libraries are recorded in `intrinsics` with their Ruby SDK
identity and dependency edges. They retain their native versions and are
not republished as renamed standard-library gems.

The caller supplies prepared archives for its target Ruby and platform.
This adapter does not compile extensions or capture an installed bundle.

### `pythonProjections`

`pythonProjections { repository; producer ? repository; runtimeRepository ? null;
dependencies ? []; runCommand; python3; }` exports `projections.python`, a
wheelhouse with a `catalog` attribute. `repository` holds the producer's wheels;
`runtimeRepository` holds its native runtime closure, excluding build tools.
Dependencies expose the same Python projection.

Owned wheels receive Nix-derived local versions. Wheels whose dependencies
change receive new identities too, and `Requires-Dist` records the selected
versions while retaining extras and markers. Wheel metadata directories, data
directories and `RECORD` hashes are updated together. Unchanged foreign wheels
retain their original bytes. Ordinary Python installers can consume the result.

### `jvmProjections`

```nix
jvmProjections {
  repository = builtPublications;
  producer = builtProject;
  dependencies = [ upstreamPackage ];
  inherit runCommand python3;
}
```

Builds a Maven repository view at `.maven`. The input repository holds
only the producer's own publications; coordinates come from its native
POMs or Ivy descriptors. `producer` defaults to `repository`. Its Nix
identity, supplied repositories, runtime selection and projection toolchain
determine the publication versions through Nix, without evaluating build
outputs. Different transformations cannot reuse the same coordinates.
Classifier jars keep their bytes.

Published POM dependencies use Maven exact-version ranges. Gradle module
metadata uses strict constraints, including reachable transitive Nix inputs.
Catalog versions remain literal artifact coordinates.

Dependencies are ordinary Nix packages exposing `projections.maven`.
Their repository files and catalogs compose transitively; incompatible
providers for the same module fail. No version-rewrite map is required.
The Maven output exposes `.catalog`, a JSON file in a separate metadata
output. It is not part of the native repository or its download lock.

`runtimeRepository` optionally supplies an already-resolved runtime closure.
It must include `purifier-runtime-modules.json`, whose `modules` array names
the selected `group`, `name` and `version` of each module. Unchanged external
components retain their coordinates and component identity through
re-exports. When an override changes a component's dependency metadata, that
component receives new coordinates too. Its projected identity depends on
its selected closure and projection toolchain, allowing equivalent diamond
dependencies to merge.

`formats = [ "maven" "ivy" ];` also exposes a native Ivy view when the
producer publishes Ivy descriptors. The default exposes only Maven.
Ivy configurations or extra attributes that cannot yet be translated
faithfully fail explicitly. Consumers must not silently drop native
configuration, variant or exclusion semantics.

`coursierInputsTool` names the directory containing `coursier_inputs.py`.
Adapters invoke it with `--cache`, `--repository`, `--catalog`, `--sources`
and optionally `--lock`. It stages private cache aliases for native version
requests, using source declarations and the locked POM graph. These aliases
are never exported as published coordinates. The adapter must rewrite its
emitted POM with `jvmRepositoryTool --rewrite-pom FILE --catalog CATALOG`.
For computed declarations, the adapter must obtain their evaluated native
coordinates before staging; the staging tool rejects unresolved matching
version expressions.

`clojureRewriteTool` selects coordinates in copied `deps.edn` files through
Clojure's native EDN reader. It leaves local and git coordinates intact.
It takes the generated EDN selection file and source directory as arguments.

The adapter must still arrange native resolution, keep supplied files out
of download locks, and publish the projected repository. These library
functions do not install native-tool hooks.

### `manifest`

```nix
manifest { name, entries }
```

A store file named `name`, made with `builtins.toFile` and without
nixpkgs. It holds a JSON object that maps the `path` of each entry to its
store path. `entries` holds file and inline entries, and any other kind
fails evaluation. The file refers to every store path it names, so one
garbage-collector root on it keeps the fetched files alive.

A file made with `builtins.toFile` cannot refer to the output of a
derivation. So the manifest names each file entry through
`builtins.fetchurl`, with the name and hash that `fetchEntry` gives it,
which is the same store path. Build the fetches first, with `fetchEntry`
and no `pkgs`. Otherwise `builtins.fetchurl` downloads each file during
evaluation, from `url` alone.

### `checkLock` and `entriesFor`

```nix
checkLock lock
entriesFor system lock
```

`lock` is the attribute set that every purifier reads its lockfiles into,
`{ purifier, entries, outputs, problems }`, as CONTRACT.md describes.
`checkLock` returns the lock when it has that shape, its entries pass the
entry check and `problems` is empty. Otherwise it fails evaluation, with
the problems joined by newlines or with the shape error. `entriesFor`
returns the entries whose `systems` is null or names `system`.

### `admits`

```nix
admits { os, cpu, libc } system
```

Whether a package with npm's `os`, `cpu` and `libc` filters installs on
the Nix system `system`. Each filter is null, a string or a list of
strings, with the meanings npm gives them:

- A null filter, an empty list and the list `[ "any" ]` admit every
  system.
- A name with a leading `!` refuses the system of that name.
- A list with names that have no `!` admits only the systems it names.
- A list of only names with a `!` admits every system it does not name.

x86_64-linux has the os `linux`, the cpu `x64` and the libc `glibc`.
aarch64-linux has the os `linux`, the cpu `arm64` and the libc `glibc`.
aarch64-darwin has the os `darwin`, the cpu `arm64` and no libc, so any
libc filter that is not null refuses it, as npm does. Any other system
fails evaluation.

### `wheels`

```nix
wheels { pythonVersion, arch, glibcVersion } fileName
```

Whether the Python wheel `fileName` installs with Python `pythonVersion`
on Linux for the CPU `arch` and glibc `glibcVersion`, read from the
compatibility tags in its file name (PEP 425 and PEP 600). `pythonVersion`
is a version such as `"3.14"`, `arch` is the CPU name of a wheel's
platform tag such as `"x86_64"` or `"aarch64"`, and `glibcVersion` is a
version such as `"2.40"`. It comes from uv-purifier.

### `wheelName`

```nix
wheelName { pythonVersion, arch, glibcVersion } lockedName
```

The file name that a wheel gets when it is built for the given Python, CPU
and glibc, from the name `lockedName` that it got when it was built on
another system. A pure wheel, whose platform tag is `any`, keeps its name.
A compiled wheel gets this Python's CPython tags. Its platform tag
`linux_<cpu>` becomes `linux_<arch>`, and its platform tag
`manylinux_2_<N>_<cpu>` becomes `manylinux_2_<glibc minor>_<arch>`. Any
other platform tag fails evaluation. It comes from poetry-purifier.

## Credits

The purifier idea was inspired by nixcloud's
[cargo (libnix)](https://github.com/nixcloud/cargo).

## License

MIT; see [LICENSE](LICENSE).
