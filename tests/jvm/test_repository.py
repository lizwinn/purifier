# SPDX-License-Identifier: MIT
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location("jvm_repository", Path(__file__).parents[2] / "tools/jvm_repository.py")
repository = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repository)


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def pom(self, source, name, dependencies="", version="1.0-SNAPSHOT"):
        path = self.root / source / "example" / name / version / f"{name}-{version}.pom"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'''<project xmlns="{repository.POM_NS}"><modelVersion>4.0.0</modelVersion>
          <groupId>example</groupId><artifactId>{name}</artifactId><version>{version}</version>
          {dependencies}</project>''')
        path.with_suffix(".jar").write_bytes(b"original jar bytes")
        return path

    def project(self, source, identity, dependencies=(), runtime=None, transformer=None):
        target = self.root / identity
        repository.project({"repository": str(self.root / source), "producerHash": identity,
                            "transformerHash": transformer,
                            "dependencies": list(dependencies),
                            "runtimeRepository": str(self.root / runtime) if runtime else None}, target / "maven", target / "ivy", target / "metadata")
        return {"maven": str(target / "maven"), "ivy": str(target / "ivy"),
                "catalog": str(target / "metadata/catalog.json")}

    def module(self, projection, name):
        return next(m for m in json.loads(Path(projection["catalog"]).read_text())["modules"] if m["name"] == name)

    def test_actual_versions_are_published_and_jar_bytes_preserved(self):
        self.pom("library", "library")
        library = self.project("library", "producer-a")
        self.assertEqual(json.loads(Path(library["catalog"]).read_text())["producer"], "producer-a")
        self.pom("application", "application", '''<dependencies><dependency><groupId>example</groupId>
            <artifactId>library</artifactId><version>[99,100)</version></dependency></dependencies>''')
        application = self.project("application", "producer-b", [library])
        pom = next(Path(application["maven"]).rglob("application-*.pom"))
        root = ET.parse(pom).getroot()
        dependency = next(e for e in root.iter() if repository.local(e) == "dependency")
        self.assertEqual(repository.text(dependency, "version"), "[1.0-nix-producer-a]")
        self.assertEqual(repository.text(root, "version"), "1.0-nix-producer-b")
        self.assertEqual(pom.with_suffix(".jar").read_bytes(), b"original jar bytes")
        self.assertTrue(Path(str(pom) + ".sha256").is_file())
        self.assertNotIn("catalog.json", [p.name for p in Path(application["maven"]).rglob("*")])

    def test_http_and_https_poms_keep_an_unprefixed_maven_root(self):
        for name, namespace in (("https", "https://maven.apache.org/POM/4.0.0"),
                                ("http", repository.POM_NS)):
            pom = self.pom(name, name)
            pom.write_text(pom.read_text().replace(repository.POM_NS, namespace))
            projection = self.project(name, name + "-build")
            published = next(Path(projection["maven"]).rglob("*.pom"))
            self.assertIn(b"<project xmlns=", published.read_bytes())
            self.assertEqual(ET.parse(published).getroot().tag, "{" + namespace + "}project")

    def test_maven4_build_pom_is_a_classified_descriptor(self):
        pom = self.pom("maven4", "library", version="1.0")
        build = pom.with_name("library-1.0-build.pom")
        build.write_text(pom.read_text().replace("<version>1.0</version>",
            "<version>${revision}</version><properties><revision>1.0</revision></properties>")
            .replace("</project>", "<build><plugins/></build></project>"))
        projection = self.project("maven4", "maven4-build")
        modules = json.loads(Path(projection["catalog"]).read_text())["modules"]
        self.assertEqual(len(modules), 1)
        revision = modules[0]["version"]
        published = Path(projection["maven"]) / "example/library" / revision / ("library-" + revision + "-build.pom")
        self.assertEqual(published.name, "library-" + revision + "-build.pom")
        root = ET.parse(published).getroot()
        self.assertEqual(repository.text(root, "version"), revision)
        self.assertIsNotNone(repository.child(root, "build"))
        self.assertTrue(published.with_name("library-" + revision + ".pom").is_file())

    def test_different_nix_builds_with_same_native_version_do_not_collide(self):
        self.pom("library", "library")
        first = self.project("library", "build-one")
        second = self.project("library", "build-two")
        self.assertNotEqual(self.module(first, "library")["version"], self.module(second, "library")["version"])

    def test_shared_diamond_is_accepted_and_conflicting_providers_fail(self):
        self.pom("library", "library")
        library = self.project("library", "library-build")
        self.pom("left", "left")
        left = self.project("left", "left-build", [library])
        self.pom("right", "right")
        right = self.project("right", "right-build", [library])
        self.pom("root", "root")
        result = self.project("root", "root-build", [left, right])
        self.assertEqual(len(json.loads(Path(result["catalog"]).read_text())["modules"]), 4)
        other = self.project("library", "other-library-build")
        with self.assertRaisesRegex(ValueError, "conflicting Nix providers for example:library"):
            self.project("root", "conflicting-root", [library, other])

    def test_parent_and_dependency_management_are_rewritten(self):
        self.pom("parent", "parent")
        parent = self.project("parent", "parent-build")
        self.pom("child", "child", '''<parent><groupId>example</groupId><artifactId>parent</artifactId>
          <version>0.1</version><relativePath>../pom.xml</relativePath></parent>
          <properties><parent.name>parent</parent.name></properties><dependencyManagement><dependencies>
          <dependency><groupId>example</groupId><artifactId>${parent.name}</artifactId><version>0.2</version>
          <type>pom</type><scope>import</scope></dependency></dependencies></dependencyManagement>''')
        child = self.project("child", "child-build", [parent])
        root = ET.parse(next(Path(child["maven"]).rglob("child-*.pom"))).getroot()
        for element in root.iter():
            if repository.local(element) in {"parent", "dependency"}:
                self.assertEqual(repository.text(element, "version"), "1.0-nix-parent-build")
        self.assertEqual(repository.text(repository.child(root, "parent"), "relativePath"), "")

    def test_gradle_module_metadata_and_classifiers_follow_selection(self):
        path = self.pom("library", "library", version="1.0")
        marker = "do_not_remove: published-with-gradle-metadata"
        path.write_text(path.read_text().replace("<modelVersion>", "<!-- " + marker + " --><modelVersion>"))
        path.with_name("library-1.0-sources.jar").write_bytes(b"sources")
        path.with_suffix(".module").write_text(json.dumps({"formatVersion": "1.1",
          "component": {"group": "example", "module": "library", "version": "1.0"},
          "variants": [{"name": "apiElements", "files": [{"name": "library-1.0.jar", "url": "library-1.0.jar"}],
            "dependencies": [{"group": "example", "module": "library", "version": {"strictly": "1.0", "rejects": ["2.0"]}}]}]}))
        projected = self.project("library", "gradle-build")
        self.assertIn(marker, next(Path(projected["maven"]).rglob("*.pom")).read_text())
        metadata = json.loads(next(Path(projected["maven"]).rglob("*.module")).read_text())
        self.assertEqual(next(iter(metadata)), "formatVersion")
        self.assertEqual(metadata["component"]["version"], "1.0-nix-gradle-build")
        self.assertEqual(metadata["variants"][0]["dependencies"][0]["version"], {"requires": "1.0-nix-gradle-build", "strictly": "1.0-nix-gradle-build"})
        self.assertTrue(next(Path(projected["maven"]).rglob("library-*-sources.jar")).is_file())
        self.assertEqual(metadata["variants"][0]["files"][0]["url"], "library-1.0-nix-gradle-build.jar")

    def test_ivy_producer_exports_maven_and_ivy_with_one_identity(self):
        source = self.root / "ivy" / "example" / "library" / "1.0"
        (source / "ivys").mkdir(parents=True)
        (source / "ivys/ivy.xml").write_text('''<ivy-module version="2.0"><info organisation="example"
            module="library" revision="1.0"/><publications><artifact name="library" type="jar" ext="jar"/>
            </publications></ivy-module>''')
        (source / "jars").mkdir()
        (source / "jars/library.jar").write_bytes(b"Ivy jar")
        result = self.project("ivy", "ivy-build")
        self.assertTrue((Path(result["maven"]) / "example/library/1.0-nix-ivy-build/library-1.0-nix-ivy-build.jar").is_file())
        descriptor = next(Path(result["ivy"]).rglob("ivy.xml"))
        self.assertEqual(repository.child(ET.parse(descriptor).getroot(), "info").get("revision"), "1.0-nix-ivy-build")

    def test_maven_source_selection_manages_transitive_inputs(self):
        self.pom("provider", "library")
        projection = self.project("provider", "provider-build")
        root = self.root / "source"
        root.mkdir()
        pom = root / "pom.xml"
        pom.write_text('<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId>'
                       '<artifactId>consumer</artifactId><version>1</version></project>')
        repository.rewrite_sources(root, projection['catalog'])
        first = pom.read_bytes()
        model = ET.parse(pom).getroot()
        managed = repository.child(repository.child(model, 'dependencyManagement'), 'dependencies')
        self.assertEqual(len(managed), 1)
        self.assertEqual(repository.text(managed[0], 'artifactId'), 'library')
        self.assertEqual(repository.text(managed[0], 'version'), '1.0-nix-provider-build')
        repository.rewrite_sources(root, projection['catalog'])
        self.assertEqual(pom.read_bytes(), first)

    def test_standard_ivy_fallback_scopes_and_exclusions(self):
        descriptor = self.root / "ivy.xml"
        descriptor.write_text('''<ivy-module version="2.0"><info organisation="example"
          module="library" revision="1"/><dependencies>
          <dependency org="example" name="compile" rev="1" conf="compile->default(compile);test->default">
            <exclude org="legacy" module="old"/>
          </dependency>
          <dependency org="example" name="runtime" rev="1" conf="runtime->default(runtime)" transitive="false"/>
          <dependency org="example" name="provided" rev="1" conf="provided->default(compile)"/>
          </dependencies></ivy-module>''')
        pom, _, _ = repository.ivy_to_pom(descriptor)
        dependencies = list(repository.child(pom, "dependencies"))
        self.assertEqual(repository.text(dependencies[0], "scope"), "")
        self.assertEqual(repository.text(dependencies[1], "scope"), "runtime")
        self.assertEqual(repository.text(dependencies[2], "scope"), "provided")
        first = next(e for e in dependencies[0].iter() if repository.local(e) == "exclusion")
        self.assertEqual(repository.text(first, "groupId"), "legacy")
        self.assertEqual(repository.text(first, "artifactId"), "old")
        second = next(e for e in dependencies[1].iter() if repository.local(e) == "exclusion")
        self.assertEqual(repository.text(second, "groupId"), "*")
        self.assertEqual(repository.text(second, "artifactId"), "*")

    def test_transitive_override_reprojects_metadata_and_preserves_diamond_identity(self):
        self.pom("provider", "library", version="2.0")
        provided = self.project("provider", "provider-build")
        selected = self.module(provided, "library")["version"]
        dependency = """<dependencies><dependency><groupId>example</groupId>
          <artifactId>library</artifactId><version>1.0</version></dependency></dependencies>"""
        self.pom("runtime", "adapter", dependency, version="1.0")
        (self.root / "runtime/purifier-runtime-modules.json").write_text(json.dumps({"modules": [
          {"group": "example", "name": "adapter", "version": "1.0"},
          {"group": "example", "name": "library", "version": selected}]}))
        app_dependency = """<dependencies><dependency><groupId>example</groupId>
          <artifactId>adapter</artifactId><version>1.0</version></dependency></dependencies>"""
        self.pom("left", "left", app_dependency)
        left = self.project("left", "left-build", [provided], runtime="runtime")
        self.pom("right", "right", app_dependency)
        right = self.project("right", "right-build", [provided], runtime="runtime")
        adapter = self.module(left, "adapter")
        self.assertNotEqual(adapter["version"], "1.0")
        self.assertEqual(adapter, self.module(right, "adapter"))
        changed_tool = self.project("left", "left-new-tool", [provided], runtime="runtime", transformer="new-projector")
        self.assertNotEqual(adapter["version"], self.module(changed_tool, "adapter")["version"])
        for projection in (left, right):
            directory = Path(projection["maven"])
            self.assertFalse((directory / "example/adapter/1.0").exists())
            pom = next(directory.rglob("adapter-*.pom"))
            dependency = next(e for e in ET.parse(pom).getroot().iter() if repository.local(e) == "dependency")
            self.assertEqual(repository.text(dependency, "version"), "[" + selected + "]")
            self.assertEqual(pom.with_suffix(".jar").read_bytes(), b"original jar bytes")
        self.pom("root", "root")
        self.project("root", "root-build", [left, right])

    def test_foreign_gradle_metadata_selection_reprojects_the_component(self):
        self.pom("provider", "library", version="2.0")
        provided = self.project("provider", "provider-build")
        selected = self.module(provided, "library")["version"]
        pom = self.pom("runtime", "adapter", version="1.0")
        pom.with_suffix('.module').write_text(json.dumps({"formatVersion": "1.1",
            "component": {"group": "example", "module": "adapter", "version": "1.0"},
            "variants": [{"name": "runtimeElements", "dependencies": [{"group": "example",
                "module": "library", "version": {"requires": "1.0"}}], "files": [
                {"name": "adapter-1.0.jar", "url": "adapter-1.0.jar"}]}]}))
        (self.root / "runtime/purifier-runtime-modules.json").write_text(json.dumps({"modules": [
            {"group": "example", "name": "adapter", "version": "1.0"},
            {"group": "example", "name": "library", "version": selected}]}))
        self.pom("application", "application", '<dependencies><dependency><groupId>example</groupId>'
            '<artifactId>adapter</artifactId><version>1.0</version></dependency></dependencies>')
        app_pom = next((self.root / 'application').rglob('*.pom'))
        app_pom.with_suffix('.module').write_text(json.dumps({
            'formatVersion': '1.1', 'component': {'group': 'example', 'module': 'application', 'version': '1.0-SNAPSHOT'},
            'variants': [{'name': 'runtime', 'dependencies': [
                {'group': 'example', 'module': 'adapter', 'version': {'requires': '1.0'}}]}]}))
        projection = self.project("application", "application-build", [provided], runtime="runtime")
        adapter = self.module(projection, "adapter")
        self.assertNotEqual(adapter['version'], '1.0')
        metadata = json.loads(next(Path(projection['maven']).rglob('adapter-*.module')).read_text())
        self.assertEqual(metadata['component']['version'], adapter['version'])
        self.assertEqual(metadata['variants'][0]['dependencies'][0]['version'], {'requires': selected, 'strictly': selected})
        self.assertEqual(metadata['variants'][0]['files'][0]['url'], 'adapter-' + adapter['version'] + '.jar')
        app_metadata = json.loads(next(Path(projection['maven']).rglob('application-*.module')).read_text())
        self.assertEqual(app_metadata['variants'][0]['dependencyConstraints'], [
            {'group': 'example', 'module': 'library', 'version': {'requires': selected, 'strictly': selected}}])
        self.assertFalse((Path(projection['maven']) / 'example/adapter/1.0').exists())

    def test_foreign_snapshots_get_content_bound_immutable_versions(self):
        self.pom('application', 'application', version='1')
        self.pom('runtime', 'snapshot', version='2-SNAPSHOT')
        (self.root/'runtime/purifier-runtime-modules.json').write_text(json.dumps({'modules': [
            {'group': 'example', 'name': 'snapshot', 'version': '2-SNAPSHOT'}]}))
        first = self.project('application', 'snapshot-first', runtime='runtime')
        selected = self.module(first, 'snapshot')['version']
        self.assertTrue(selected.startswith('2-nix-'))
        self.assertNotIn('SNAPSHOT', selected)
        jar = self.root/'runtime/example/snapshot/2-SNAPSHOT/snapshot-2-SNAPSHOT.jar'
        jar.write_bytes(b'changed upstream snapshot')
        second = self.project('application', 'snapshot-second', runtime='runtime')
        self.assertNotEqual(selected, self.module(second, 'snapshot')['version'])

    def test_private_ivy_tool_dependencies_do_not_become_maven_runtime_dependencies(self):
        source = self.root / "ivy" / "example" / "java" / "1"
        (source / "ivys").mkdir(parents=True)
        (source / "ivys/ivy.xml").write_text("""<ivy-module version="2.0"><info organisation="example"
          module="java" revision="1"/><configurations><conf name="compile" visibility="public"/>
          <conf name="scala-tool" visibility="private"/></configurations><dependencies>
          <dependency org="org.scala-lang" name="scala-compiler" rev="2.13.16"
            conf="scala-tool->default,optional(default)"/></dependencies></ivy-module>""")
        (source / "jars").mkdir()
        (source / "jars/java.jar").write_bytes(b"Java library")
        result = self.project("ivy", "ivy-build")
        pom = ET.parse(next(Path(result["maven"]).rglob("*.pom"))).getroot()
        self.assertEqual([e for e in pom.iter() if repository.local(e) == "dependency"], [])

    def test_invalid_coordinates_cannot_escape_the_repository(self):
        for coordinate in [("../outside", "library", "1"), ("example", "../outside", "1"),
                           ("example", "library", "/outside"), (".example", "library", "1")]:
            with self.assertRaisesRegex(ValueError, "invalid published JVM"):
                repository.validate_coordinates(*coordinate)

    def test_unsupported_ivy_semantics_fail_instead_of_losing_them(self):
        source = self.root / "ivy"
        source.mkdir()
        (source / "ivy.xml").write_text('''<ivy-module version="2.0"><info organisation="example" module="lib" revision="1"/>
          <dependencies><dependency org="other" name="lib" rev="1" conf="custom->unusual"/></dependencies></ivy-module>''')
        with self.assertRaisesRegex(ValueError, "cannot be represented faithfully"):
            self.project("ivy", "ivy-build")


if __name__ == "__main__":
    unittest.main()
