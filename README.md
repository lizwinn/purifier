# purifier

purifier is the Nix library that the nix-purifiers share. Each purifier
writes a lockfile that lists the downloads a build needs, as download
entries. This library turns each entry into a store path, and lays a list
of entries out as one directory tree. It also holds the platform and wheel
helpers that more than one purifier needs.

The nix-purifiers family targets x86_64-linux and aarch64-linux.

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
fetcher of the same name. The default `fetchcvs` sets `CVSREADONLYFS`, so
cvs writes no lock files into the repository it exports from.

`pkgs` may be left out, for an evaluation without nixpkgs. A file entry is
then fetched with Nix's built-in fetcher, and its store path is the store
path that nixpkgs' `fetchurl` gives it. So a sandboxed build with `pkgs`
and a command without nixpkgs share the fetched files. An inline entry
needs no fetcher. Every other kind fails evaluation without `pkgs` or its
own fetcher.

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
Any other system fails evaluation.

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

The entry kinds and the tree of fetched entries follow the design of
[sbt-nix-backend](https://github.com/clhodapp/sbt-nix-backend).

## License

MIT; see [LICENSE](LICENSE).
