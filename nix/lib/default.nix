# SPDX-License-Identifier: MIT
#
# The functions of the `purifier` library namespace. README.md describes
# each of them.
{ lib }:
{
  lockedProjectInputs = import ./locked-project-inputs.nix { inherit lib; };
  projectInputsTool = ../../tools;
  npmProjections = import ./npm-projections.nix { inherit lib; };
  npmRepositoryTool = ../../tools/npm_repository.py;
  pythonProjections = import ./python-projections.nix { inherit lib; };
  pythonRepositoryTool = ../../tools/python_repository.py;
  jvmProjections = import ./jvm-projections.nix { inherit lib; };
  coursierInputsTool = ../../tools;
  clojureRewriteTool = ../../tools/rewrite_clojure.clj;
  jvmRepositoryTool = ../../tools/jvm_repository.py;
  inputIdentity = import ./input-identity.nix { inherit lib; };
  projectInputs = import ./project-inputs.nix { inherit lib; };
  fetchEntry = import ./fetch.nix { inherit lib; };
  tree = import ./tree.nix { inherit lib; };
  manifest = import ./manifest.nix { inherit lib; };
  inherit (import ./lock.nix { inherit lib; }) checkLock entriesFor;
  admits = import ./admits.nix { inherit lib; };
  wheels = import ./wheels.nix;
  wheelName = args: import ./wheel-name.nix ({ inherit lib; } // args);
}
