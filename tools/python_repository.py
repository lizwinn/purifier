# SPDX-License-Identifier: MIT
"""Export immutable Python wheels with dependency metadata for ordinary installers."""
import argparse
import base64
import csv
from email import policy
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version


def wheel(path):
    name, version, _, _ = parse_wheel_filename(path.name)
    with zipfile.ZipFile(path) as archive:
        metadata = [p for p in archive.namelist() if p.endswith('.dist-info/METADATA')]
        if len(metadata) != 1:
            raise ValueError(f'wheel needs one metadata directory: {path}')
        model = BytesParser(policy=policy.compat32).parsebytes(archive.read(metadata[0]))
    if canonicalize_name(model['Name']) != name or Version(model['Version']) != version:
        raise ValueError(f'wheel filename disagrees with metadata: {path}')
    return name, str(version), metadata[0].split('/')[0], model


def requirement(text, selected):
    original = Requirement(text)
    target = selected.get(canonicalize_name(original.name))
    if target is None:
        return text
    extras = '[' + ','.join(sorted(original.extras)) + ']' if original.extras else ''
    marker = '; ' + str(original.marker) if original.marker is not None else ''
    return original.name + extras + '==' + target + marker


def rewrite(source, target, version, selected):
    _, _, directory, model = wheel(source)
    model.replace_header('Version', version)
    dependencies = model.get_all('Requires-Dist', [])
    del model['Requires-Dist']
    for value in dependencies:
        model['Requires-Dist'] = requirement(value, selected)
    new_directory = directory.removesuffix('.dist-info').rsplit('-', 1)[0] + '-' + version + '.dist-info'
    old_data, new_data = directory.removesuffix('.dist-info') + '.data', new_directory.removesuffix('.dist-info') + '.data'
    files = {}
    with zipfile.ZipFile(source) as archive:
        for entry in archive.infolist():
            if entry.is_dir() or entry.filename in {directory + '/' + name for name in ['RECORD', 'RECORD.jws', 'RECORD.p7s']}:
                continue
            name = entry.filename
            if name.startswith(directory + '/'):
                name = new_directory + name[len(directory):]
            elif name.startswith(old_data + '/'):
                name = new_data + name[len(old_data):]
            data = model.as_bytes(policy=policy.compat32.clone(max_line_length=0)) if name == new_directory + '/METADATA' else archive.read(entry)
            if name in files:
                raise ValueError(f'duplicate wheel entry: {name}')
            files[name] = data, entry.external_attr
    record = io.StringIO(newline='')
    writer = csv.writer(record, lineterminator='\n')
    for name, (data, _) in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        writer.writerow([name, 'sha256=' + digest, len(data)])
    record_name = new_directory + '/RECORD'
    writer.writerow([record_name, '', ''])
    files[record_name] = record.getvalue().encode(), 0o100644 << 16
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, (data, attributes) in sorted(files.items()):
            entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = attributes
            archive.writestr(entry, data)


def project(spec, destination, catalog):
    """The adapter supplies only the native runtime closure, excluding build tools."""
    inputs, versions, owned, provided = {}, {}, set(), set()
    for kind in ['dependencies', 'runtime', 'repository']:
        for repository in spec.get(kind, []):
            for path in sorted(Path(repository).glob('*.whl')):
                name, version, _, model = wheel(path)
                if name in versions and versions[name] != version:
                    raise ValueError(f'conflicting Python providers for {name}')
                versions[name] = version
                inputs.setdefault(name, []).append((path, model))
                if kind == 'dependencies':
                    provided.add(name)
                if kind == 'repository':
                    owned.add(name)
    if owned & provided:
        raise ValueError("a Python publication duplicates a supplied project")
    changed = set(owned)
    while True:
        following = changed | {name for name, values in inputs.items()
            if name not in provided and any(canonicalize_name(Requirement(req).name) in (changed | provided)
                for _, model in values for req in model.get_all('Requires-Dist', []))}
        if following == changed:
            break
        changed = following
    selected = {name: str(Version(version).public) + '+nix.' + spec['identity'] if name in changed else version
        for name, version in versions.items()}
    destination.mkdir(parents=True, exist_ok=True)
    publications = []
    for name, values in sorted(inputs.items()):
        filenames = set()
        for source, _ in values:
            parts = source.name.split('-')
            parts[1] = selected[name]
            target = destination / '-'.join(parts)
            if name in changed:
                temporary = destination / ('.' + target.name)
                rewrite(source, temporary, selected[name], selected)
                contents = temporary.read_bytes()
                temporary.unlink()
            else:
                contents = source.read_bytes()
            if target.exists() and target.read_bytes() != contents:
                raise ValueError(f'conflicting Python wheel: {target.name}')
            target.write_bytes(contents)
            filenames.add(target.name)
        publications.append({'name': name, 'version': selected[name], 'wheels': sorted(filenames)})
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps({'version': 1, 'modules': publications}, sort_keys=True) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--catalog', required=True, type=Path)
    args = parser.parse_args()
    project(json.loads(args.spec.read_text()), args.destination, args.catalog)


if __name__ == '__main__':
    main()
