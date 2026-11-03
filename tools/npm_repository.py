# SPDX-License-Identifier: MIT
"""Export Nix-built npm artifacts with exact native runtime dependency metadata."""
import argparse
import base64
from collections import deque
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def document(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def version(source, identity):
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?', source):
        raise ValueError('npm artifact has no valid native version: ' + source)
    public = source.split('+')[0]
    if '-' in public:
        identifiers = public.split('-', 1)[1].split('.')
        if any(not item or (item.isdigit() and len(item) > 1 and item.startswith('0')) for item in identifiers):
            raise ValueError('npm artifact has an invalid prerelease version: ' + source)
    if '+' in source and any(not item for item in source.split('+', 1)[1].split('.')):
        raise ValueError('npm artifact has invalid version metadata: ' + source)
    return public + ('.' if '-' in public else '-') + 'nix.h' + identity


def read_archive(path):
    result = {}
    with tarfile.open(path, 'r:*') as archive:
        for entry in archive.getmembers():
            name = PurePosixPath(entry.name)
            if not name.parts or name.parts[0] != 'package' or '..' in name.parts:
                raise ValueError('npm archive path escapes package: ' + entry.name)
            if entry.isdir():
                continue
            if not (entry.isfile() or entry.issym() or entry.islnk()):
                raise ValueError('unsupported npm archive member: ' + entry.name)
            if entry.name in result:
                raise ValueError('duplicate npm archive member: ' + entry.name)
            contents = archive.extractfile(entry).read() if entry.isfile() else None
            result[entry.name] = entry, contents
    if 'package/package.json' not in result:
        raise ValueError('npm archive has no package.json: ' + str(path))
    return result


def rewrite(source, target, metadata):
    entries = read_archive(source)
    # Exact exported dependency metadata replaces the producer's native lock.
    entries.pop('package/npm-shrinkwrap.json', None)
    entries.pop('package/package-lock.json', None)
    entries = {name: entry for name, entry in entries.items()
               if not name.startswith('package/node_modules/')}
    entries['package/package.json'] = entries['package/package.json'][0], document(metadata) + b'\n'
    with target.open('wb') as output, gzip.GzipFile(filename='', mode='wb', fileobj=output, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
            for name, (original, contents) in sorted(entries.items()):
                entry = tarfile.TarInfo(name)
                entry.mode = 0o755 if original.mode & 0o111 else 0o644
                entry.uid = entry.gid = entry.mtime = 0
                entry.type, entry.linkname = original.type, original.linkname
                entry.size = len(contents) if contents is not None else 0
                archive.addfile(entry, io.BytesIO(contents) if contents is not None else None)


def supplied(repositories):
    modules, primaries = {}, {}
    for view in repositories:
        root = Path(view['repository'])
        catalog = json.loads(Path(view['catalog']).read_text())
        if catalog['version'] != 1:
            raise ValueError('unsupported npm projection catalog')
        for module in catalog['modules']:
            key = module['name'], module['version']
            path = root / module['archive']
            metadata = json.loads(read_archive(path)['package/package.json'][1])
            if (metadata.get('name'), metadata.get('version')) != key:
                raise ValueError('npm catalog disagrees with archive: ' + str(path))
            integrity = 'sha512-' + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()
            if integrity != module['integrity']:
                raise ValueError('npm catalog integrity disagrees with archive: ' + str(path))
            previous = modules.get(key)
            if previous and previous['path'].read_bytes() != path.read_bytes():
                raise ValueError('conflicting npm providers: ' + '@'.join(key))
            modules[key] = module | {'path': path}
            if module['primary']:
                other = primaries.get(module['name'])
                if other and other != module['version']:
                    raise ValueError('conflicting primary npm providers: ' + module['name'])
                primaries[module['name']] = module['version']
    return modules, primaries


def fingerprint(start, nodes, owned, provided, identity, projector, runtime):
    # Canonicalize the reachable graph from this node, independently of the
    # producer's filesystem paths. Cycles and distinct peer contexts retain
    # their edges without requiring a cyclic cryptographic fixed point.
    identifiers = {start: 0}
    pending = deque([start])
    graph = []
    while pending:
        key = pending.popleft()
        node = nodes[key]
        coordinate = node['name'], node['version']
        if coordinate in provided:
            graph.append({'provided': coordinate, 'integrity': provided[coordinate]['integrity']})
            continue
        entry = {'name': node['name'], 'version': node['version'], 'content': node['contentHash']}
        if key in owned:
            entry['producer'] = identity
        if node.get('native'):
            entry['native'] = {'kind': node['native'], 'os': runtime['os'], 'cpu': runtime['cpu']}
            if node['native'] == 'node-addon':
                entry['native']['nodeMajor'] = runtime['nodeMajor']
        entry['dependencies'] = []
        for name, edge in sorted(node.get('dependencies', {}).items()):
            target = edge['target']
            if target not in nodes:
                raise ValueError('npm dependency has no captured native node: ' + target)
            if target not in identifiers:
                identifiers[target] = len(identifiers)
                pending.append(target)
            entry['dependencies'].append([name, edge['type'], identifiers[target]])
        graph.append(entry)
    return digest(document({"projector": projector, "graph": graph}))


def normalize_package_bin(metadata):
    # package.json accepts a single executable as a string; registry manifests
    # use its command map. This is also safe for reused immutable archives.
    if isinstance(metadata.get('bin'), str):
        metadata['bin'] = {metadata['name'].rsplit('/', 1)[-1]: metadata['bin']}
    return metadata


def project(spec, destination, catalog):
    graph = json.loads(Path(spec['graph']).read_text())
    if graph['version'] != 1:
        raise ValueError('unsupported npm native graph')
    nodes, roots = graph['nodes'], graph['primaries']
    owned = set(roots.values())
    selected_roots = spec.get('roots', sorted(roots))
    missing = set(selected_roots) - set(roots)
    if missing:
        raise ValueError('unknown npm publications: ' + ', '.join(sorted(missing)))
    provided, _ = supplied(spec.get('dependencies', []))
    reachable, pending = set(), [roots[name] for name in selected_roots]
    while pending:
        key = pending.pop()
        if key in reachable:
            continue
        reachable.add(key)
        pending.extend(edge['target'] for edge in nodes[key].get('dependencies', {}).values())
    selected = {}
    for key in reachable:
        node = nodes[key]
        coordinate = node['name'], node['version']
        selected[key] = (node['version'] if coordinate in provided else
            version(node['version'], fingerprint(key, nodes, owned, provided, spec['identity'],
                spec.get('projectorIdentity') or digest(Path(__file__).read_bytes()), graph)))
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'tarballs').mkdir(exist_ok=True)
    publications = {}
    for key in sorted(reachable):
        node = nodes[key]
        coordinate = node['name'], selected[key]
        filename = 'tarballs/' + digest(document(coordinate)) + '.tgz'
        target = destination / filename
        provided_key = node['name'], node['version']
        metadata = normalize_package_bin(dict(node['package']))
        if provided_key in provided:
            target.write_bytes(provided[provided_key]['path'].read_bytes())
            metadata = normalize_package_bin(json.loads(read_archive(provided[provided_key]['path'])['package/package.json'][1]))
        else:
            metadata['version'] = selected[key]
            for field in ('dependencies', 'optionalDependencies', 'peerDependencies'):
                entries = {}
                for name in metadata.get(field, {}):
                    edge = node.get('dependencies', {}).get(name)
                    if edge is None:
                        if field == 'dependencies' and name not in metadata.get('optionalDependencies', {}):
                            raise ValueError(f'npm runtime dependency was not captured: {node["name"]} -> {name}')
                        continue
                    dependency = nodes[edge['target']]
                    chosen = selected[edge['target']]
                    entries[name] = chosen if name == dependency['name'] else 'npm:' + dependency['name'] + '@' + chosen
                if field in metadata:
                    metadata[field] = entries
            metadata.pop('devDependencies', None)
            metadata['gypfile'] = False
            metadata.pop('hasInstallScript', None)
            metadata.pop('bundleDependencies', None)
            metadata.pop('bundledDependencies', None)
            # Installation/build hooks already ran in the Nix producer. The
            # exported artifact contains their outputs, including native code.
            scripts = dict(metadata.get('scripts', {}))
            for name in ('preinstall', 'install', 'postinstall', 'prepare', 'prepack', 'postpack', 'prepublish', 'prepublishOnly'):
                scripts.pop(name, None)
            if 'scripts' in metadata:
                metadata['scripts'] = scripts
            if node.get('native'):
                metadata['os'] = [graph['os']]
                metadata['cpu'] = [graph['cpu']]
                if node['native'] == 'node-addon':
                    engines = dict(metadata.get('engines', {}))
                    compatible = '^' + str(graph['nodeMajor']) + '.0.0'
                    engines['node'] = ' || '.join(clause.strip() + ' ' + compatible
                        for clause in engines.get('node', '*').split('||'))
                    metadata['engines'] = engines
            metadata['nixPurifier'] = {'version': 1, 'sourceVersion': node['version']}
            rewrite(Path(spec['archives']) / node['archive'], target, metadata)
        contents = target.read_bytes()
        source_version = provided[provided_key].get('sourceVersion', node['version']) if provided_key in provided else node['version']
        module = {'name': coordinate[0], 'version': coordinate[1], 'sourceVersion': source_version, 'archive': filename,
                  'integrity': 'sha512-' + base64.b64encode(hashlib.sha512(contents).digest()).decode(),
                  'primary': key in {roots[name] for name in selected_roots}, 'package': metadata}
        old = publications.get(coordinate)
        if old and old['integrity'] != module['integrity']:
            raise ValueError('conflicting projected npm bytes: ' + '@'.join(coordinate))
        if old:
            module['primary'] = old['primary'] or module['primary']
        publications[coordinate] = module
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps({'version': 1, 'modules': [publications[key] for key in sorted(publications)]}, sort_keys=True) + '\n')


def install_repository(spec, destination, catalog):
    """Adapt dependency metadata for native installers without resolver hooks.

    This view is only for installation. Publication retains the original
    immutable archives and their native version requirements.
    """
    modules, _ = supplied(spec['views'])
    publications = []
    destination.mkdir(parents=True, exist_ok=True)
    for key, module in sorted(modules.items()):
        metadata = json.loads(read_archive(module['path'])['package/package.json'][1])
        for field in ('dependencies', 'optionalDependencies', 'peerDependencies'):
            for alias, wanted in list(metadata.get(field, {}).items()):
                match = re.fullmatch(r'npm:(@[^/]+/[^@]+|[^@]+)@(.+)', wanted)
                dependency = modules.get((match[1], match[2]) if match else (alias, wanted))
                if dependency:
                    metadata[field][alias] = 'file:' + str(destination / dependency['archive'])
        target = destination / module['archive']
        target.parent.mkdir(parents=True, exist_ok=True)
        rewrite(module['path'], target, metadata)
        entry = {name: value for name, value in module.items() if name != 'path'}
        entry['package'] = metadata
        entry['integrity'] = 'sha512-' + base64.b64encode(hashlib.sha512(target.read_bytes()).digest()).decode()
        publications.append(entry)
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps({'version': 1, 'modules': publications}, sort_keys=True) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    args = parser.parse_args()
    (install_repository if args.install else project)(json.loads(args.spec.read_text()), args.destination, args.catalog)


if __name__ == '__main__':
    main()
