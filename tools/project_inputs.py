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
      source = builtins.fetchTree ((builtins.removeAttrs (builtins.parseFlakeRef input.url) [ "dir" ])
        // { narHash = input.narHash; });
      hasHash = builtins.match ".*narHash=.*" input.url != null;
      hasQuery = builtins.match ".*[?].*" input.url != null;
      lockedUrl = input.url + (if hasHash then "" else
        (if hasQuery then "&" else "?") + "narHash=" + input.narHash);
      flake = builtins.getFlake lockedUrl;
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
    in assert source.narHash == input.narHash; package'''


def maven_views(inputs, roots=None):
    result = []
    for index, entry in enumerate(inputs):
        env = dict(os.environ, PURIFIER_PROJECT_INPUT=json.dumps(entry))
        expression = 'let package = (' + selection_expression() + ''');
          repository = package.projections.maven;
          in [ repository repository.metadata ]'''
        root_options = ['--no-link']
        if roots is not None:
            Path(roots).mkdir(parents=True, exist_ok=True)
            root_options = ['--out-link', str(Path(roots).resolve() / str(index))]
        builds = nix_json('build', '--impure', *root_options, '--json', '--expr', expression, env=env)
        outputs = {name: path for build in builds for name, path in build['outputs'].items()}
        repository, metadata = outputs['out'], outputs['metadata']
        result.append({'repository': Path(repository), 'catalog': Path(metadata) / 'catalog.json'})
    return result


def stage_coursier(cache, views, sources, lock=None):
    """Return exact supplied cache paths, keeping tool-only versions lockable."""
    import coursier_inputs
    import jvm_repository
    jvm_repository.read_catalogs([{'catalog': str(view['catalog'])} for view in views])
    aliases = set()
    for view in views:
        aliases.update(coursier_inputs.stage(cache, view['repository'], view['catalog'], sources, lock))
    return aliases


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    pins = commands.add_parser('pin')
    pins.add_argument('--previous', type=Path)
    pins.add_argument('--input', action='append')
    pins.add_argument('--refresh', action='store_true')
    pins.add_argument('--output', type=Path, required=True)
    repository = commands.add_parser('maven')
    repository.add_argument('--inputs', type=Path, required=True)
    repository.add_argument('--destination', type=Path, required=True)
    repository.add_argument('--metadata', type=Path, required=True)
    repository.add_argument('--roots', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'pin':
        previous = []
        if args.previous is not None and args.previous.is_file():
            previous = json.loads(args.previous.read_text()).get('projectInputs', [])
        references = args.input if args.input is not None else [entry['reference'] for entry in previous]
        args.output.write_text(json.dumps(pin(references, previous, args.refresh)))
    else:
        import jvm_repository as jvm
        views = maven_views(json.loads(args.inputs.read_text()), args.roots)
        modules = jvm.read_catalogs([{'catalog': str(view['catalog'])} for view in views])
        paths = set()
        args.destination.mkdir(parents=True, exist_ok=True)
        args.metadata.mkdir(parents=True, exist_ok=True)
        for view in views:
            jvm.merge(view['repository'], args.destination)
            paths.update(path.relative_to(view['repository']).as_posix()
                         for path in view['repository'].rglob('*') if path.is_file())
        catalog = args.metadata / 'catalog.json'
        catalog.write_text(json.dumps({'version': 1, 'modules': list(modules.values())}))
        (args.metadata / 'supplied-paths.json').write_text(json.dumps(sorted(paths)))
        (args.metadata / 'project-inputs.edn').write_text(jvm.clojure_config(catalog))


if __name__ == '__main__':
    main()
