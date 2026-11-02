# SPDX-License-Identifier: MIT
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[2] / 'tools'))
import coursier_runtime


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / 'cache'
        self.catalog = self.root / 'catalog.json'
        self.catalog.write_text(json.dumps({'version': 1, 'modules': [{
            'group': 'example', 'name': 'provided', 'originalVersion': '0',
            'version': '0-nix-input', 'producer': 'input'}]}))

    def pom(self, name, version, body=''):
        directory = self.cache / 'https/repo/example' / name / version
        directory.mkdir(parents=True)
        path = directory / f'{name}-{version}.pom'
        path.write_text(f'<project><groupId>example</groupId><artifactId>{name}</artifactId>'
                        f'<version>{version}</version>{body}</project>')
        return path

    def test_selected_runtime_preserves_parent_bom_and_nix_identity(self):
        self.pom('parent', '7', '<properties><bom.version>3</bom.version></properties>')
        self.pom('bom', '3')
        app = self.pom('adapter', '1', '''<parent><groupId>example</groupId><artifactId>parent</artifactId><version>7</version></parent>
          <dependencyManagement><dependencies><dependency><groupId>example</groupId><artifactId>bom</artifactId>
          <version>${bom.version}</version><type>pom</type><scope>import</scope></dependency></dependencies></dependencyManagement>''')
        app.with_suffix('.jar').write_bytes(b'foreign runtime')
        supplied = self.pom('provided', '99')
        supplied.with_suffix('.jar').write_bytes(b'Nix alias')
        # A bootstrap-only artifact in the same cache must not be exported.
        self.pom('tool', '9').with_suffix('.jar').write_bytes(b'bootstrap')
        output = self.root / 'runtime'
        coursier_runtime.export(self.cache, {'app.resolvedRunMvnDeps': [
            'qref:v1:hash:' + str(app.with_suffix('.jar')), str(supplied.with_suffix('.jar'))]}, self.catalog, output)
        self.assertTrue((output / 'example/parent/7/parent-7.pom').is_file())
        self.assertTrue((output / 'example/bom/3/bom-3.pom').is_file())
        self.assertEqual((output / 'example/adapter/1/adapter-1.jar').read_bytes(), b'foreign runtime')
        self.assertFalse((output / 'example/provided').exists())
        self.assertFalse((output / 'example/tool').exists())
        graph = json.loads((output / 'purifier-runtime-modules.json').read_text())
        self.assertEqual(graph['modules'], [{'group': 'example', 'name': 'adapter', 'version': '1'},
                                            {'group': 'example', 'name': 'provided', 'version': '0-nix-input'}])

    def test_maven4_build_pom_does_not_replace_its_consumer_pom(self):
        pom = self.pom('library', '1')
        jar = pom.with_suffix('.jar')
        jar.write_bytes(b'library')
        build = pom.with_name('library-1-build.pom')
        build.write_text('<project><groupId>example</groupId><artifactId>library</artifactId>'
                         '<version>${revision}</version><build><plugins/></build></project>')
        output = self.root / 'runtime'
        coursier_runtime.export(self.cache, [str(jar)], self.catalog, output)
        target = output / 'example/library/1'
        self.assertEqual((target / pom.name).read_bytes(), pom.read_bytes())
        self.assertEqual((target / build.name).read_bytes(), build.read_bytes())

    def test_url_escaped_native_versions_use_normal_maven_names(self):
        pom = self.pom('native', '2.13+0.5')
        encoded = pom.parent.with_name('2.13%2B0.5')
        pom.parent.rename(encoded)
        pom = encoded/'native-2.13%2B0.5.pom'
        (encoded/'native-2.13+0.5.pom').rename(pom)
        jar = pom.with_suffix('.jar')
        jar.write_bytes(b'scala native')
        output = self.root/'runtime'
        coursier_runtime.export(self.cache, [str(jar)], self.catalog, output)
        self.assertEqual((output/'example/native/2.13+0.5/native-2.13+0.5.jar').read_bytes(), b'scala native')
        self.assertTrue((output/'example/native/2.13+0.5/native-2.13+0.5.pom').is_file())

    def test_timestamped_snapshot_uses_its_selected_descriptor(self):
        pom = self.pom('snapshot', '1-SNAPSHOT')
        pom.unlink()
        for timestamp in ('20260101.120000-1', '20260102.120000-2'):
            candidate = pom.with_name('snapshot-1-'+timestamp+'.pom')
            candidate.write_text('<project><groupId>example</groupId><artifactId>snapshot</artifactId>'
                '<version>1-SNAPSHOT</version><description>'+timestamp+'</description></project>')
        jar = pom.with_name('snapshot-1-20260102.120000-2-tests.jar')
        jar.write_bytes(b'selected snapshot')
        output = self.root/'runtime'
        coursier_runtime.export(self.cache, [str(jar)], self.catalog, output)
        target = output/'example/snapshot/1-SNAPSHOT'
        self.assertEqual((target/'snapshot-1-SNAPSHOT-tests.jar').read_bytes(), b'selected snapshot')
        self.assertIn('20260102.120000-2', (target/'snapshot-1-SNAPSHOT.pom').read_text())

    def test_conflicting_native_versions_are_rejected(self):
        jars = []
        for version in ('1', '2'):
            jar = self.pom('library', version).with_suffix('.jar')
            jar.write_bytes(b'jar')
            jars.append(str(jar))
        with self.assertRaisesRegex(ValueError, 'conflicting selected native runtime versions'):
            coursier_runtime.export(self.cache, jars, self.catalog, self.root / 'runtime')


if __name__ == '__main__':
    unittest.main()
