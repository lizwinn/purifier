# The purifier contract

Every nix-purifier meets this contract, so that a developer who knows one
purifier knows them all. The contract covers the Nix interface, the
commands, the checks and the README. Each purifier's lockfile format stays
its own, because each one models its own ecosystem.

In this document, `<tool>` is the purifier's tool name as its repository
names it, such as `cargo` in `cargo-purifier`, and `<Tool>` is the same name
capitalized, such as `Cargo`.

## The builder

A purifier's flake publishes one builder in its library:

```nix
lib.<tool>-purifier.build<Tool>Project {
  pkgs,
  src,
  lockFile ? src + "/<tool>-purifier.lock.json",
  fetchers ? { },
  # and the purifier's own arguments
}
```

By default the purifier lockfile lies in the project's tree, next to the
build tool's own lockfile. A project can instead keep its build external:
the build tool's own lockfile, such as Cargo.lock or package-lock.json,
stays in the tree, and everything a purifier adds lives outside it, kept
by the flake that calls the builder, which passes the purifier lockfile as
`lockFile`. In an ecosystem with no lockfile of its own, an external build
keeps the purifier lockfile outside the tree too. A `lockFile` outside
`src` takes the place of any purifier lockfile in `src`.

`fetchers` is passed unchanged to `fetchEntry` of the `purifier` library,
and to `tree`. A company mirror or credentials go into `fetchers.fetchurl`,
and that one hook works the same way in every purifier.

The builder's result has these attributes:

- `packages.<name>`: what the project builds, under names the purifier
  chooses for its ecosystem.
- `tests`: the project's test run in the sandbox.
- `release`: the artifact that `<tool>-purifier publish` uploads, built by
  Nix.
- `fetches`: the `purifier` library entries that the build fetches. SBOM,
  license and vulnerability tools read this list in every ecosystem.

Every attribute name in the result is known at evaluation time, with no
import-from-derivation.

## The lock in memory

Every purifier reads its lockfiles, the build tool's and its own, into one
attribute set of the same shape, whatever their formats on disk:

```nix
lib.<tool>-purifier.readLock { src, lockFile ? ... }
# =>
{
  purifier = "<tool>-purifier";
  entries = [ /* download entries of the purifier library */ ];
  outputs = { /* <name> = { ... }, every name known at evaluation */ };
  problems = [ /* why the lockfiles cannot be used, each naming the fix */ ];
}
```

Each entry in `entries` may carry `systems`, a list of Nix systems,
when it is needed only there. An entry without `systems` is needed
everywhere. `problems` is empty for a usable lock, and holds a message for
each stale, tampered, malformed or incomplete lockfile.

The builder passes the result to `checkLock` from the purifier library.
`checkLock` fails evaluation with the problems or with a shape error. The
builder takes the downloads of one system from `entriesFor`, and its
`fetches` attribute is the entries of the lock. A tool that reads every
purifier, such as an SBOM generator, calls `readLock` and reads this one
shape.

## The lockfile

A purifier reuses the build tool's own lockfile wherever it records the
downloads. It writes `<tool>-purifier.lock.json`, next to the build
tool's lockfile or at the external path, only for what the build tool's
lockfile cannot record. A purifier whose build
tool records everything writes no lockfile of its own.

Every purifier lockfile holds a format `version` and a hash of its own
content. A stale, tampered, malformed or incomplete lockfile fails, in the
command and at Nix evaluation, with a message that names the command that
fixes it.

## The commands

Every purifier has these commands. Where the build tool has a plugin
mechanism, the purifier is a plugin, and the command is a thin wrapper
around the plugin's task.

- `<tool>-purifier lock` runs the build tool and records the downloads. It
  keeps the build tool's resolution as it is, writing the build tool's
  lockfile only where it is missing or incomplete. A relock of an
  unchanged project reproduces the lockfile byte for byte.
- `<tool>-purifier update [<package>...]` updates every lockfile in one
  run: it re-resolves through the build tool's own update, such as
  `cargo update` or `uv lock --upgrade`, and then relocks the purifier
  lockfile against the result. With package names, it updates only those
  packages, as the build tool's update does. A build tool with no lockfile
  of its own re-resolves its version ranges and dynamic versions, and the
  purifier lockfile is the one lockfile that changes.
- `<tool>-purifier prefetch` realizes, in the Nix store, everything the
  lockfile lists for the current system that day-to-day development needs:
  the build's downloads, the test dependencies, the toolchain, and what
  the editor asks for, such as source jars and language-server workers. It
  keeps them alive with a garbage-collector root beside the purifier
  lockfile. After a prefetch, the everyday loop and the editor work with
  the network cut off.
- `<tool>-purifier publish` uploads the `release` that Nix built.

Each command finds the purifier lockfile next to the build tool's own
lockfile. For an external build, it takes `--lockfile <path>` or the
environment variable `<TOOL>_PURIFIER_LOCKFILE`, such as
`CARGO_PURIFIER_LOCKFILE`, and either one takes precedence over the
default. The dev shell of the flake that calls the builder can set the
variable to the path it passes as `lockFile`, so a developer in that shell
runs the commands with no flag.

The everyday loop goes through the build tool's own command, such as
`cargo build` or `sbt test`, with the locked downloads. Commands that need
a download not in the lock stop with a message that names
`<tool>-purifier lock`.

## The checks

Every purifier's flake has these checks, on x86_64-linux and
aarch64-linux, and its CI runs them on both:

| Check | What it proves |
| --- | --- |
| `conformance-stale` | A lockfile older than the project fails, naming the command to run. |
| `conformance-tampered` | A lockfile with a changed hash fails. |
| `conformance-missing` | A lockfile without a needed entry fails, naming the command to run. |
| `conformance-malformed` | A lockfile that is not valid fails, naming the command to run. |
| `conformance-relock` | Relocking a fixture reproduces its lockfile byte for byte. |
| `conformance-rebuild` | `nix build --rebuild` of a fixture gives the same output. |
| `conformance-offline` | After `prefetch`, the everyday loop of a fixture works with no network. |
| `conformance-update` | `update` on a fixture that pins an old version moves it, and both lockfiles then pass the stale check. |
| `conformance-external` | A fixture kept external, whose `src` holds only the build tool's own files, builds from a purifier lockfile outside it. |

A check that needs the network, such as a relock, runs in CI as a job of
the same name when the sandbox cannot run it.

## The README

Every purifier's README has these sections, in this order:

1. Quickstart
2. Everyday loop
3. Offline work
4. Editor
5. Errors and fixes
6. Sources supported and refused
7. Real-world projects
8. Credits
