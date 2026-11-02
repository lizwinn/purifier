# SPDX-License-Identifier: MIT
import base64
import csv
from email import policy
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import python_repository as repository


def fixture(directory, name, version='1.0', requires=(), data=False):
    directory.mkdir(parents=True, exist_ok=True)
    info = name + '-' + version + '.dist-info'
    contents = {
        name + '/__init__.py': 'VALUE = ' + repr(name) + '\n',
        info + '/METADATA': 'Metadata-Version: 2.1\nName: ' + name + '\nVersion: ' + version + '\n'
            + 'Provides-Extra: feature\n' + ''.join('Requires-Dist: ' + req + '\n' for req in requires) + '\n',
        info + '/WHEEL': 'Wheel-Version: 1.0\nGenerator: purifier-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
    }
    if data:
        contents[name + '-' + version + '.data/data/example.txt'] = 'payload\n'
    output = io.StringIO(newline='')
    record = csv.writer(output, lineterminator='\n')
    for path, content in contents.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content.encode()).digest()).rstrip(b'=').decode()
        record.writerow([path, 'sha256=' + digest, len(content.encode())])
    record.writerow([info + '/RECORD', '', ''])
    contents[info + '/RECORD'] = output.getvalue()
    wheel = directory / (name + '-' + version + '-py3-none-any.whl')
    with zipfile.ZipFile(wheel, 'w') as archive:
        for path, content in contents.items():
            archive.writestr(path, content)
    return wheel


class ProjectionTests(unittest.TestCase):
    def test_transitive_override_and_ordinary_installer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root / 'library', 'library', data=True)
            repository.project({'repository': [str(root / 'library')], 'identity': 'a' * 64},
                root / 'provided', root / 'provided.json')
            fixture(root / 'runtime', 'helper', requires=['library[feature]>=99; python_version >= "3"'])
            fixture(root / 'consumer', 'consumer', requires=['helper>=1'])
            spec = {'repository': [str(root / 'consumer')], 'runtime': [str(root / 'runtime')],
                    'dependencies': [str(root / 'provided')], 'identity': 'b' * 64}
            repository.project(spec, root / 'result', root / 'result.json')
            modules = {m['name']: m for m in json.loads((root / 'result.json').read_text())['modules']}
            selected = repository.selections([{'repository': str(root / 'result'), 'catalog': str(root / 'result.json')}])
            self.assertEqual(set(selected), {'library', 'helper', 'consumer'})
            self.assertEqual(selected['library']['version'], '1.0+nix.' + 'a' * 64)
            self.assertEqual(modules['library']['version'], '1.0+nix.' + 'a' * 64)
            self.assertEqual(modules['helper']['version'], '1.0+nix.' + 'b' * 64)
            provided = next((root / 'provided').glob('*.whl'))
            self.assertEqual(provided.read_bytes(), (root / 'result' / provided.name).read_bytes())
            for path in (root / 'result').glob('*.whl'):
                with zipfile.ZipFile(path) as archive:
                    record = next(name for name in archive.namelist() if name.endswith('/RECORD'))
                    for name, digest, size in csv.reader(io.StringIO(archive.read(record).decode())):
                        if not digest:
                            self.assertEqual(name, record)
                            continue
                        contents = archive.read(name)
                        self.assertEqual(int(size), len(contents))
                        self.assertEqual(digest, 'sha256=' + base64.urlsafe_b64encode(
                            hashlib.sha256(contents).digest()).rstrip(b'=').decode())
            subprocess.run([sys.executable, '-m', 'pip', '--isolated', 'install', '--no-index',
                '--find-links', str(root / 'result'), '--target', str(root / 'installed'),
                'consumer==' + modules['consumer']['version']], check=True,
                env=dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK='1'))
            subprocess.run([sys.executable, '-S', '-c',
                'import consumer, helper, library; assert library.VALUE == "library"'],
                env=dict(os.environ, PYTHONPATH=str(root / 'installed')), check=True)
            self.assertEqual((root / 'installed/example.txt').read_text(), 'payload\n')
            repository.project(spec, root / 'again', root / 'again.json')
            self.assertEqual({p.name: p.read_bytes() for p in (root / 'result').iterdir()},
                             {p.name: p.read_bytes() for p in (root / 'again').iterdir()})

    def test_incompatible_supplied_wheel_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = fixture(root, 'library')
            incompatible = root / original.name.replace('-py3-', '-py2-')
            original.rename(incompatible)
            catalog = root / 'catalog.json'
            catalog.write_text(json.dumps({'version': 1, 'modules': [
                {'name': 'library', 'version': '1.0', 'wheels': [incompatible.name]}]}))
            with self.assertRaisesRegex(ValueError, 'no supplied wheel installs'):
                repository.selections([{'repository': str(root), 'catalog': str(catalog)}])

    def test_conflicting_native_versions_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root / 'wheels', 'library', '1.0')
            fixture(root / 'wheels', 'library', '2.0')
            with self.assertRaisesRegex(ValueError, 'conflicting Python providers'):
                repository.project({'repository': [str(root / 'wheels')], 'identity': 'a' * 64},
                    root / 'result', root / 'result.json')

    def test_unmodified_runtime_wheel_keeps_original_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root / 'own', 'consumer', requires=['helper>=1'])
            original = fixture(root / 'runtime', 'helper')
            repository.project({'repository': [str(root / 'own')], 'runtime': [str(root / 'runtime')],
                'identity': 'a' * 64}, root / 'result', root / 'result.json')
            self.assertEqual(original.read_bytes(), (root / 'result' / original.name).read_bytes())


if __name__ == '__main__':
    unittest.main()
