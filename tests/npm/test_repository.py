# SPDX-License-Identifier: MIT
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import npm_repository as repository


def node(root, key, name, dependencies=None, contents=None, bin=None):
    metadata = {'name': name, 'version': '1.0.0', 'main': 'index.js',
                'scripts': {'install': 'exit 99'}, 'dependencies': dependencies or {}}
    if bin is not None:
        metadata['bin'] = bin
    files = {'package.json': json.dumps(metadata).encode(), 'index.js': (contents or "module.exports='locked';\n").encode()}
    archive = root / (key + '.tgz')
    with tarfile.open(archive, 'w:gz') as output:
        for filename, data in files.items():
            entry = tarfile.TarInfo('package/' + filename)
            entry.size = len(data)
            output.addfile(entry, io.BytesIO(data))
    return {'name': name, 'version': '1.0.0', 'package': metadata, 'archive': archive.name,
            'contentHash': hashlib.sha256(repository.document({name: hashlib.sha256(data).hexdigest() for name, data in files.items()})).hexdigest(), 'dependencies': {}}


def graph(root):
    a = node(root, 'a', 'app', {'library': '>=99', 'alias': 'npm:helper@>=99'},
             "module.exports=require('library')+':'+require('alias');\n", bin="index.js")
    b = node(root, 'b', 'library', {'helper': '>=99'}, "module.exports='library '+require('helper');\n")
    c = node(root, 'c', 'helper')
    a['dependencies'] = {'library': {'type': 'prod', 'target': 'b'}, 'alias': {'type': 'prod', 'target': 'c'}}
    b['dependencies'] = {'helper': {'type': 'prod', 'target': 'c'}}
    return {'version': 1, 'os': 'linux', 'cpu': 'x64', 'nodeMajor': 24,
            'primaries': {'app': 'a'}, 'nodes': {'a': a, 'b': b, 'c': c}}


class ProjectionTests(unittest.TestCase):
    def project(self, root, value, label='result', identity='a' * 64, dependencies=()):
        source = root / (label + '-graph.json')
        source.write_text(json.dumps(value))
        destination, catalog = root / label, root / (label + '.json')
        repository.project({'graph': str(source), 'archives': str(root), 'identity': identity,
                            'dependencies': dependencies}, destination, catalog)
        return destination, catalog, json.loads(catalog.read_text())['modules']

    def test_native_installer_exact_graph_aliases_and_determinism(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = graph(root)
            destination, catalog, modules = self.project(root, value)
            versions = {module['name']: module['version'] for module in modules}
            self.assertTrue(all('-nix.h' in version for version in versions.values()))
            for module in modules:
                entries = repository.read_archive(destination / module['archive'])
                metadata = json.loads(entries['package/package.json'][1])
                self.assertNotIn('install', metadata['scripts'])
            metadata = json.loads(repository.read_archive(destination / next(module['archive'] for module in modules
                if module['name'] == 'app'))['package/package.json'][1])
            self.assertEqual(metadata['bin'], {'app':'index.js'})
            self.assertEqual(metadata['dependencies']['library'], versions['library'])
            self.assertEqual(metadata['dependencies']['alias'], 'npm:helper@' + versions['helper'])
            again, _, _ = self.project(root, value, label='again')
            self.assertEqual({path.name: path.read_bytes() for path in (destination / 'tarballs').iterdir()},
                             {path.name: path.read_bytes() for path in (again / 'tarballs').iterdir()})
            class Registry(BaseHTTPRequestHandler):
                def do_GET(self):
                    route = unquote(urlsplit(self.path).path).lstrip('/')
                    if route.startswith('tarballs/'):
                        path = destination / route
                        if not path.is_file():
                            self.send_error(404)
                            return
                        body = path.read_bytes()
                    else:
                        entries = [module for module in modules if module['name'] == route]
                        if not entries:
                            self.send_error(404)
                            return
                        manifests = {}
                        for module in entries:
                            manifest = json.loads(repository.read_archive(destination / module['archive'])['package/package.json'][1])
                            manifest['dist'] = {'tarball': 'http://' + self.headers['Host'] + '/' + module['archive'],
                                                'integrity': module['integrity']}
                            manifests[module['version']] = manifest
                        body = json.dumps({'name': route, 'versions': manifests,
                                           'dist-tags': {'latest': entries[0]['version']}}).encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                def log_message(self, *args):
                    pass
            server = ThreadingHTTPServer(('127.0.0.1', 0), Registry)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                consumer = root / 'consumer'
                consumer.mkdir()
                (consumer / 'package.json').write_text('{"name":"native-consumer","private":true}')
                env = dict(os.environ, npm_config_cache=str(root / 'cache'), npm_config_update_notifier='false')
                npm = os.environ['NPM']
                subprocess.run([npm, 'install', '--ignore-scripts', '--no-audit', '--no-fund',
                    '--registry', f'http://127.0.0.1:{server.server_port}/', 'app@' + versions['app']],
                    cwd=consumer, env=env, check=True)
                self.assertTrue((consumer/'node_modules/.bin/app').is_file())
                subprocess.run([str(Path(npm).parent / 'node'), '-e',
                    "require('node:assert/strict').equal(require('app'),'library locked:locked')"],
                    cwd=consumer, env=env, check=True)
            finally:
                server.shutdown()
                server.server_close()

    def test_cycles_and_foreign_identity_independent_of_producer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = graph(root)
            first, _, modules = self.project(root, value)
            second, _, other = self.project(root, value, label='second', identity='b' * 64)
            first_versions, second_versions = ({module['name']: module['version'] for module in group} for group in (modules, other))
            self.assertNotEqual(first_versions['app'], second_versions['app'])
            self.assertEqual(first_versions['library'], second_versions['library'])
            self.assertEqual(first_versions['helper'], second_versions['helper'])
            value['nodes']['c']['dependencies'] = {'library': {'type': 'prod', 'target': 'b'}}
            value['nodes']['c']['package']['dependencies'] = {'library': '*'}
            self.project(root, value, label='cycle')

    def test_native_abi_is_part_of_the_immutable_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = graph(root)
            value['nodes']['c']['native'] = 'node-addon'
            value['nodes']['c']['package']['engines'] = {'node': '>=20 <26 || >=28'}
            first, _, modules = self.project(root, value)
            helper = next(module for module in modules if module['name'] == 'helper')
            metadata = json.loads(repository.read_archive(first / helper['archive'])['package/package.json'][1])
            self.assertEqual(metadata['engines']['node'], '>=20 <26 ^24.0.0 || >=28 ^24.0.0')
            self.assertFalse(metadata['gypfile'])
            value['nodeMajor'] = 22
            _, _, other = self.project(root, value, label='other-abi')
            changed = next(module for module in other if module['name'] == 'helper')
            self.assertNotEqual(helper['version'], changed['version'])

    def test_target_abi_changes_identity_but_helper_runtime_does_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = graph(root)
            value['nodes']['c']['native'] = 'node-addon'
            value['nodeAbi'] = '72'
            _, _, first = self.project(root, value)
            value['helperRuntime'] = {'version': '99.0.0', 'abi': '999'}
            _, _, second = self.project(root, value, label='other-helper')
            self.assertEqual([item['version'] for item in first], [item['version'] for item in second])
            value['nodeAbi'] = '73'
            _, _, third = self.project(root, value, label='other-target-abi')
            first_versions = {item['name']: item['version'] for item in first}
            third_versions = {item['name']: item['version'] for item in third}
            self.assertNotEqual(first_versions['helper'], third_versions['helper'])

    def test_namespace_versions_follow_native_semver(self):
        self.assertRegex(repository.version('1.2.3-beta.1+build', 'a' * 64), r'^1[.]2[.]3-beta[.]1[.]nix[.]h')
        for invalid in ('1.0', '01.0.0', '1.0.0-01', '1.0.0-a..b', '1.0.0+build.'):
            with self.assertRaises(ValueError):
                repository.version(invalid, 'a' * 64)

    def test_long_names_remain_native_registry_coordinates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            name = 'a' * 210
            value = {'version': 1, 'primaries': {name: 'long'},
                     'nodes': {'long': node(root, 'long', name)}}
            destination, _, modules = self.project(root, value)
            self.assertEqual(modules[0]['name'], name)
            self.assertLess(len(Path(modules[0]['archive']).name), 255)
            self.assertTrue((destination / modules[0]['archive']).is_file())

    def test_supplied_archives_preserve_exact_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = graph(root)
            provided, catalog, modules = self.project(root, value)
            item = next(module for module in modules if module['name'] == 'helper')
            source = root / value['nodes']['c']['archive']
            source.write_bytes((provided / item['archive']).read_bytes())
            value['nodes']['c']['version'] = item['version']
            value['nodes']['c']['package'] = json.loads(repository.read_archive(source)['package/package.json'][1])
            # Supply only helper, so app and library remain publications of
            # the new project rather than supplied duplicates.
            only_helper = root / 'helper-catalog.json'
            only_helper.write_text(json.dumps({'version': 1, 'modules': [item]}))
            result, _, output = self.project(root, value, label='consumer', identity='c' * 64,
                dependencies=[{'repository': str(provided), 'catalog': str(only_helper)}])
            helper = next(module for module in output if module['name'] == 'helper')
            self.assertEqual(item['version'], helper['version'])
            self.assertEqual((provided / item['archive']).read_bytes(), (result / helper['archive']).read_bytes())


if __name__ == '__main__':
    unittest.main()
