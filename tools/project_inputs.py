# SPDX-License-Identifier: MIT
"""Let Nix pin and realize native repository inputs without artifact download locks."""
import json
import os
from pathlib import Path
import subprocess


def nix_json(*arguments, env=None):
    return json.loads(subprocess.check_output(['nix', *arguments], text=True, env=env))


def pin(references, previous=(), refresh=False):
    old = {entry['reference']: entry for entry in previous}
    result = []
    for reference in dict.fromkeys(references):
        if reference in old and not refresh:
            result.append(old[reference])
            continue
        source, separator, attribute = reference.partition('#')
        metadata = nix_json('flake', 'metadata', '--json', '--no-write-lock-file', source)
        if not metadata.get('locked', {}).get('narHash'):
            raise ValueError(f'Nix did not supply a content hash for {reference}')
        result.append({'reference': reference, 'url': metadata['url'],
                       'narHash': metadata['locked']['narHash'], 'attribute': attribute if separator else 'default'})
    return result


def selection_expression(variable='PURIFIER_PROJECT_INPUT'):
    # Pass user strings through JSON/environment, never interpolate Nix code.
    return '''let
      input = builtins.fromJSON (builtins.getEnv "''' + variable + '''");
      flake = builtins.getFlake input.url;
      system = builtins.currentSystem;
      path = builtins.filter builtins.isString (builtins.split "[.]" input.attribute);
      has = keys: value: keys == [] || (builtins.isAttrs value && builtins.hasAttr (builtins.head keys) value
        && has (builtins.tail keys) value.${builtins.head keys});
      get = keys: value: if keys == [] then value
        else get (builtins.tail keys) value.${builtins.head keys};
      package = if has ([ "packages" system ] ++ path) flake
        then get ([ "packages" system ] ++ path) flake
        else if has ([ "legacyPackages" system ] ++ path) flake
        then get ([ "legacyPackages" system ] ++ path) flake else get path flake;
    in assert flake.sourceInfo.narHash == input.narHash; package'''


def maven_views(inputs):
    result = []
    for entry in inputs:
        env = dict(os.environ, PURIFIER_PROJECT_INPUT=json.dumps(entry))
        expression = 'let package = (' + selection_expression() + ''');
          repository = package.projections.maven;
          in [ repository repository.metadata ]'''
        builds = nix_json('build', '--impure', '--no-link', '--json', '--expr', expression, env=env)
        outputs = {name: path for build in builds for name, path in build['outputs'].items()}
        repository, metadata = outputs['out'], outputs['metadata']
        result.append({'repository': Path(repository), 'catalog': Path(metadata) / 'catalog.json'})
    return result
