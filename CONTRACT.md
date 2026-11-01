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
  lockFile,
  fetchers ? { },
  # and the purifier's own arguments
}
```

The build tool's own lockfile, such as Cargo.lock or package-lock.json,
lives in the project's tree, where the build tool keeps it. Everything a
purifier adds lives outside the tree: `lockFile` is the purifier lockfile,
kept by the flake that calls the builder, and the builder reads nothing a
purifier wrote from `src`. An ecosystem with no lockfile of its own has
only the purifier lockfile, outside the tree too.

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

## The lockfile

A purifier reuses the build tool's own lockfile wherever it records the
downloads. It writes `<tool>-purifier.lock.json` next to the build tool's
lockfile only for what that lockfile cannot record. A purifier whose build
tool records everything writes no lockfile of its own.

Every purifier lockfile holds a format `version` and a hash of its own
content. A stale, tampered, malformed or incomplete lockfile fails, in the
command and at Nix evaluation, with a message that names the command that
fixes it.

## The commands

Every purifier has these commands. Where the build tool has a plugin
mechanism, the purifier is a plugin, and the command is a thin wrapper
around the plugin's task.

- `<tool>-purifier lock` runs the build tool and records the downloads. A
  relock of an unchanged project reproduces the lockfile byte for byte.
- `<tool>-purifier prefetch` realizes, in the Nix store, everything the
  lockfile lists for the current system that day-to-day development needs:
  the build's downloads, the test dependencies, the toolchain, and what
  the editor asks for, such as source jars and language-server workers. It
  keeps them alive with a garbage-collector root in the project. After a
  prefetch, the everyday loop and the editor work with the network cut off.
- `<tool>-purifier publish` uploads the `release` that Nix built.

Each command finds the purifier lockfile from `--lockfile <path>`, or
else from the environment variable `<TOOL>_PURIFIER_LOCKFILE`, such as
`CARGO_PURIFIER_LOCKFILE`. The dev shell of the flake that calls the
builder sets the variable to the same path it passes as `lockFile`, so a
developer in that shell runs the commands with no flag. A command that
needs the purifier lockfile and finds neither stops with a message that
says so.

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
| `conformance-external` | Every fixture's `src` holds only the build tool's own files, and the purifier lockfile lies outside it. |

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
