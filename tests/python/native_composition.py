#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exercise native Python integrations with a compiled Python 3.12 Nix input."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path.cwd().resolve()
family = sys.argv[1]
system = subprocess.check_output([
    'nix', 'eval', '--impure', '--raw', '--expr', 'builtins.currentSystem',
], text=True).strip()
snapshot = json.loads(subprocess.check_output([
    'nix', 'flake', 'metadata', '--json', '--no-write-lock-file', 'path:' + str(root),
], text=True))['path']
# The producer includes a real CPython extension and uses an immutable revision.
producer = 'github:nix-purifiers/uv-purifier/0316132adbe05cc9d68fc1bf70d0d8d24f7cfab1'
reference = producer + '#checks.' + system + '.composition-producer-python312'

with tempfile.TemporaryDirectory(prefix=family + '-target-python-') as temporary:
    temporary = Path(temporary)
    subprocess.run([
        'nix-store', '--add-root', str(temporary / 'source'), '--realise', snapshot,
    ], check=True)
    attribute = 'cli' if family in ('pip', 'pipenv') else 'default'
    output = Path(subprocess.check_output([
        'nix', 'build', str(root) + '#' + attribute, '--out-link',
        str(temporary / 'cli'), '--print-out-paths',
    ], text=True).strip())
    command = [output / 'bin' / ('poetry' if family == 'poetry' else family + '-purifier')]
    if family == 'poetry':
        command += ['purifier']
    env = dict(os.environ, PYTHON_TARGET_CONSUMER=snapshot,
               UV_PYTHON_DOWNLOADS='never', NO_COLOR='1')
    env.pop('FORCE_COLOR', None)
    expression = '''let
      f = builtins.getFlake ("path:" + builtins.getEnv "PYTHON_TARGET_CONSUMER");
      p = f.inputs.nixpkgs.legacyPackages.${builtins.currentSystem};
    in p.runCommand "python312-consumer-tools" {} ''
      mkdir -p "$out/bin"
      ln -s ${p.python312.interpreter} "$out/bin/python"
      ln -s ${p.python312.interpreter} "$out/bin/python3"
      ln -s ${p.uv}/bin/uv "$out/bin/uv"
    '' '''
    tools = Path(subprocess.check_output([
        'nix', 'build', '--impure', '--expr', expression, '--out-link',
        str(temporary / 'tools'), '--print-out-paths',
    ], env=env, text=True).strip())
    env['PATH'] = str(tools / 'bin') + ':' + env['PATH']
    project = temporary / 'project'
    project.mkdir()
    subprocess.run([
        str(tools / 'bin/python'), '-m', 'venv', str(project / '.venv'),
    ], check=True)
    if family == 'pip':
        (project / 'requirements.txt').write_text('simple>=99\n')
    elif family == 'pipenv':
        (project / 'Pipfile').write_text(
            '[packages]\nsimple=">=99"\n[requires]\npython_version="3.12"\n')
    else:
        source = project / 'src/target_python'
        source.mkdir(parents=True)
        (source / '__init__.py').write_text('import simple\n')
        definition = '''[project]
name = "target-python"
version = "1.0"
requires-python = ">=3.12,<3.13"
dependencies = ["simple>=99"]
'''
        if family == 'poetry':
            definition += '''[tool.poetry]
packages = [{include = "target_python", from = "src"}]
[build-system]
requires = ["poetry-core>=2.0"]
build-backend = "poetry.core.masonry.api"
'''
        else:
            definition += '''[build-system]
requires = ["setuptools>=70"]
build-backend = "setuptools.build_meta"
'''
        (project / 'pyproject.toml').write_text(definition)

    def run(*arguments, **kwargs):
        return subprocess.run([str(argument) for argument in arguments],
                              cwd=project, env=env, check=True, **kwargs)

    arguments = ['lock', '--input', reference]
    if family in ('pip', 'pipenv', 'poetry'):
        arguments += ['--python', str(tools / 'bin/python')]
    if family == 'pip':
        arguments += ['--system', system]
    run(*command, *arguments)
    run(*command, 'prefetch')
    if family == 'poetry':
        run(command[0], 'sync')
    result = run(project / '.venv/bin/python', '-I', '-c',
                 'import sys,markupsafe._speedups;'
                 'assert sys.version_info[:2]==(3,12);'
                 'print(markupsafe._speedups.__file__)', capture_output=True, text=True)
    assert result.stdout.strip().endswith('.so'), result.stdout
    print(family.upper() + ' COMPILED PYTHON 3.12 INPUT USING THE PURIFIER SDK PASSED')
