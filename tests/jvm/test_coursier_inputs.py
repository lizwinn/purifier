# SPDX-License-Identifier: MIT
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).parents[2] / "tools"))
import coursier_inputs
import jvm_repository


class CoursierInputsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.repository = self.root / "repository"
        self.sources = self.root / "sources"
        self.sources.mkdir()
        self.version = "0.0.1-nix-provider"
        self.catalog = self.root / "catalog.json"
        self.catalog.write_text(json.dumps({"version": 1, "modules": [{"group": "example", "name": "library_3",
          "version": self.version, "originalVersion": "0.0.1", "producer": "provider"}]}))
        self.actual = self.repository / "example/library_3" / self.version
        self.pom(self.actual, "example", "library_3", self.version)
        (self.actual / f"library_3-{self.version}.jar").write_bytes(b"Nix-selected library")

    def pom(self, directory, group, name, version, dependencies=""):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}-{version}.pom"
        path.write_text(f'''<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>
          <groupId>{group}</groupId><artifactId>{name}</artifactId><version>{version}</version>
          {dependencies}</project>''')
        return path

    def stage(self):
        coursier_inputs.stage(self.cache, self.repository, self.catalog, self.sources)

    def test_requested_range_alias_uses_nix_bytes_without_republishing_alias(self):
        (self.sources / "build.mill").write_text('def mvnDeps = Seq(mvn"example::library:[99,100)")')
        self.stage()
        alias = self.cache / "https/repo1.maven.org/maven2/example/library_3/99/library_3-99"
        self.assertEqual(alias.with_suffix(".jar").read_bytes(), b"Nix-selected library")
        self.assertEqual(jvm_repository.text(ET.parse(alias.with_suffix(".pom")).getroot(), "version"), "99")
        canonical = self.actual / f"library_3-{self.version}.pom"
        self.assertEqual(jvm_repository.text(ET.parse(canonical).getroot(), "version"), self.version)
        self.assertFalse((self.repository / "example/library_3/99").exists())

    def test_build_tool_version_and_store_backed_file_are_preserved(self):
        (self.sources / "build.mill").write_text('def mvnDeps = Seq(mvn"example::library:4.1")')
        root = self.cache / "https/repo1.maven.org/maven2"
        tool = root / "example/library_3/4.4"
        self.pom(tool, "example", "library_3", "4.4")
        (tool / "library_3-4.4.jar").write_bytes(b"Mill's own library")
        self.pom(root / "tool/bootstrap/1", "tool", "bootstrap", "1", '''<dependencies><dependency>
          <groupId>example</groupId><artifactId>library_3</artifactId><version>4.4</version>
          </dependency></dependencies>''')
        original = self.root / "store-jar"
        original.write_bytes(b"original locked artifact")
        requested = root / "example/library_3/4.1/library_3-4.1.jar"
        requested.parent.mkdir(parents=True)
        requested.symlink_to(original)
        self.stage()
        self.assertEqual((tool / "library_3-4.4.jar").read_bytes(), b"Mill's own library")
        self.assertEqual(original.read_bytes(), b"original locked artifact")
        self.assertEqual(requested.read_bytes(), b"Nix-selected library")

    def test_transitive_requests_come_from_project_graph(self):
        (self.sources / "project.scala").write_text('//> using dep app:adapter:1')
        root = self.cache / "https/internal.example/maven"
        self.pom(root / "app/adapter/1", "app", "adapter", "1", '''<dependencies><dependency>
          <groupId>example</groupId><artifactId>library_3</artifactId><version>5</version>
          </dependency></dependencies>''')
        self.stage()
        self.assertEqual((root / "example/library_3/5/library_3-5.jar").read_bytes(), b"Nix-selected library")


if __name__ == "__main__":
    unittest.main()
