# SPDX-License-Identifier: MIT
"""Present Nix-selected artifacts at Coursier's requested coordinates.

Aliases exist only in the writable build cache. Published repositories keep
immutable Nix coordinates; emitted POMs must use the projection catalog.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import jvm_repository as jvm

CHECKSUMS = {".sha1", ".sha256", ".sha512", ".md5", ".asc"}
SOURCE_DIRECTORIES = {".git", "out", "target", ".scala-build", ".scala-cli-purifier", ".mill-purifier", "node_modules"}
COORDINATE = re.compile(r'([A-Za-z0-9_.+\-]+)(:{1,3})([A-Za-z0-9_.+\-]+):([\[(][^\s";]*[\])]|[^\s",;]+)')


def candidate(version, original):
    if version.startswith(("[", "(")):
        body = version[1:-1]
        lower = body.split(",", 1)[0].strip()
        if not lower:
            return "0"
        return lower if version.startswith("[") else lower + ".1"
    if version.endswith("+"):
        return version[:-1] + "0"
    if version.startswith("latest."):
        return original
    # Interpolated native declarations require the evaluated dependency data.
    if "$" in version or "{" in version or "}" in version:
        return None
    return version


def source_requests(sources, modules, lock=None):
    requests = {coordinate: {module["originalVersion"], module["version"]}
                for coordinate, module in modules.items()}
    texts = []
    for directory, subdirectories, files in os.walk(sources):
        subdirectories[:] = [name for name in subdirectories if name not in SOURCE_DIRECTORIES]
        for name in files:
            path = Path(directory) / name
            if path.suffix in {".scala", ".sc", ".mill", ".java", ".md"}:
                texts.append(path.read_text(errors="replace"))
    if lock is not None:
        texts.append(Path(lock).read_text())
    declarations = set()
    for contents in texts:
        for group, crossing, name, version in COORDINATE.findall(contents):
            declarations.add((group, crossing, name, version))
            for coordinate, module in modules.items():
                if group != module["group"]:
                    continue
                matches = name == module["name"] if crossing == ":" else module["name"].startswith(name + "_")
                if matches:
                    selected = candidate(version, module["originalVersion"])
                    if selected:
                        jvm.validate_coordinates(group, module["name"], selected)
                        requests[coordinate].add(selected)
                    else:
                        raise ValueError(f"native evaluated dependency data is needed for {group}:{name}:{version}")
    return requests, declarations


def replace(path, contents):
    """Never write through store symlinks; retain unchanged cache mtimes."""
    def write(destination, data):
        if not destination.is_symlink() and destination.is_file() and destination.read_bytes() == data:
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.unlink(missing_ok=True)
        destination.write_bytes(data)

    write(path, contents)
    for algorithm in ("sha1", "md5"):
        checksum = Path(str(path) + "." + algorithm)
        write(checksum, (hashlib.new(algorithm, contents).hexdigest() + "\n").encode())


def stage(cache, repository, catalog, sources, lock=None):
    cache, repository = Path(cache), Path(repository)
    modules = jvm.read_catalogs([{"catalog": str(catalog)}])
    requests, declarations = source_requests(sources, modules, lock)
    roots = {cache / "https/repo1.maven.org/maven2", cache / "https/repo.maven.apache.org/maven2"}
    # Ask native metadata for the cache's repository roots and transitive
    # constraints, rather than assuming every project uses Maven Central.
    records = {}
    for path in sorted(cache.rglob("*.pom")):
        try:
            root, properties, (group, name, version) = jvm.pom_model(path)
        except (ValueError, ET.ParseError):
            continue
        records[group, name, version] = root, properties
        suffix = Path(group.replace(".", "/")) / name / version / f"{name}-{version}.pom"
        if path.as_posix().endswith("/" + suffix.as_posix()):
            roots.add(Path(path.as_posix()[:-len(suffix.as_posix())]))
    pending = []
    for group, crossing, name, constraint in declarations:
        for coordinate in records:
            native_group, native_name, native_version = coordinate
            matches = name == native_name if crossing == ":" else native_name.startswith(name + "_")
            if group == native_group and matches and candidate(constraint, native_version) == native_version:
                pending.append(coordinate)
    seen = set()
    while pending:
        coordinate = pending.pop()
        if coordinate in seen:
            continue
        seen.add(coordinate)
        root, properties = records[coordinate]
        # Only the project graph supplies aliases. The same cache also holds
        # Mill's own libraries, which belong to a separate class loader.
        dependencies = jvm.child(root, "dependencies")
        if dependencies is None:
            continue
        for dependency in dependencies:
            if jvm.text(dependency, "scope") in {"test", "provided", "system"} or jvm.text(dependency, "optional") == "true":
                continue
            target = (jvm.expand(jvm.text(dependency, "groupId"), properties),
                      jvm.expand(jvm.text(dependency, "artifactId"), properties))
            requested = jvm.expand(jvm.text(dependency, "version"), properties)
            selected = candidate(requested, modules[target]["originalVersion"] if target in modules else requested)
            if selected:
                if target in modules:
                    jvm.validate_coordinates(*target, selected)
                    requests[target].add(selected)
                elif (*target, selected) in records:
                    pending.append((*target, selected))
    aliases = set()
    for coordinate, module in modules.items():
        group, name = coordinate
        actual = module["version"]
        source = repository / group.replace(".", "/") / name / actual
        if not source.is_dir():
            raise ValueError(f"Nix projection has no Maven directory for {group}:{name}:{actual}")
        for root in sorted(roots):
            directory = root / group.replace(".", "/") / name
            for requested in sorted(requests[coordinate]):
                for artifact in sorted(source.iterdir()):
                    if (not artifact.is_file() or artifact.suffix in CHECKSUMS
                            or not artifact.name.startswith(name + "-" + actual)):
                        continue
                    filename = jvm.renamed(artifact.name, name, actual, requested)
                    contents = artifact.read_bytes()
                    if artifact.suffix == ".pom":
                        model = ET.fromstring(contents)
                        jvm.put(model, "version", requested)
                        contents = ET.tostring(model, encoding="utf-8", xml_declaration=True)
                    elif artifact.suffix == ".module":
                        alias = {**module, "originalVersion": actual, "version": requested}
                        contents = jvm.module_metadata(artifact, alias, modules)
                    target = directory / requested / filename
                    replace(target, contents)
                    aliases.add(target.relative_to(cache).as_posix())
            metadata = ET.Element("metadata")
            for field, value in (("groupId", group), ("artifactId", name)):
                ET.SubElement(metadata, field).text = value
            versioning = ET.SubElement(metadata, "versioning")
            ET.SubElement(versioning, "latest").text = actual
            ET.SubElement(versioning, "release").text = actual
            versions = ET.SubElement(versioning, "versions")
            for requested in sorted(requests[coordinate]):
                ET.SubElement(versions, "version").text = requested
            ET.SubElement(versioning, "lastUpdated").text = "19800101000000"
            target = directory / "maven-metadata.xml"
            replace(target, ET.tostring(metadata, encoding="utf-8", xml_declaration=True))
            aliases.add(target.relative_to(cache).as_posix())
    return aliases


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("cache", "repository", "catalog", "sources"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--lock", type=Path)
    args = parser.parse_args()
    try:
        stage(args.cache, args.repository, args.catalog, args.sources, args.lock)
    except (ValueError, KeyError, ET.ParseError) as error:
        parser.exit(1, f"purifier: {error}\n")


if __name__ == "__main__":
    main()
