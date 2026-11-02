#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Real Nix source verification for native and builder project-input selection."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / 'tools'))
import project_inputs

system = subprocess.check_output(['nix', 'eval', '--impure', '--raw', '--expr',
                                  'builtins.currentSystem'], text=True).strip()
with tempfile.TemporaryDirectory(prefix='purifier-source-validation-') as temporary:
    producer = Path(temporary) / 'producer'
    producer.mkdir()
    child = producer / 'child'
    child.mkdir()
    for directory, marker in [(producer, 'root'), (child, 'child')]:
        (directory / 'flake.nix').write_text('{ outputs = { self }: { packages.' + system
            + '.library = { marker = "' + marker + '"; }; }; }')
    for suffix, marker in [('', 'root'), ('?dir=child', 'child')]:
        entry = project_inputs.pin(['path:' + str(producer) + suffix + '#library'])[0]
        environment = dict(os.environ, PURIFIER_PROJECT_INPUT=json.dumps(entry),
                           PURIFIER_TEST_ROOT=str(root))
        expressions = [
            '(' + project_inputs.selection_expression() + ').marker',
            '''let
              f = builtins.getFlake ("path:" + builtins.getEnv "PURIFIER_TEST_ROOT");
              select = import (f.outPath + "/nix/lib/locked-project-inputs.nix") {lib=f.inputs.nixpkgs.lib;};
              input = builtins.fromJSON (builtins.getEnv "PURIFIER_PROJECT_INPUT");
            in (builtins.head (select {inputs=[input];system=builtins.currentSystem;})).marker''',
        ]
        for expression in expressions:
            result = subprocess.check_output(['nix', 'eval', '--impure', '--raw', '--expr', expression],
                                             text=True, env=environment)
            assert result == marker
            tampered = dict(entry, narHash='sha256-' + 'A' * 43 + '=')
            result = subprocess.run(['nix', 'eval', '--impure', '--raw', '--expr', expression],
                text=True, capture_output=True,
                env=dict(environment, PURIFIER_PROJECT_INPUT=json.dumps(tampered)))
            assert result.returncode, 'a modified source hash was accepted'
            assert 'mismatch' in result.stderr or 'hash' in result.stderr.lower(), result.stderr
    print('NIX PROJECT INPUT SOURCE HASHES VERIFIED')
