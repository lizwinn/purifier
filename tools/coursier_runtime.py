# SPDX-License-Identifier: MIT
"""Export a native Coursier classpath with its Maven descriptor closure."""
import json
from pathlib import Path
import re
from urllib.parse import unquote
import xml.etree.ElementTree as ET

import jvm_repository as jvm


def artifact_name(path, coordinate):
    """Normalize URL-escaped cache paths and unique Maven snapshot names."""
    _, name, version = coordinate
    filename = unquote(path.name)
    prefix = name + '-' + version
    if filename.startswith(prefix + '.') or filename.startswith(prefix + '-'):
        return filename
    if version.endswith('-SNAPSHOT'):
        timestamped = re.fullmatch(re.escape(name + '-' + version[:-len('SNAPSHOT')])
                                  + r'\d{8}\.\d{6}-\d+((?:-[^.]+)?\.[^/]+)', filename)
        if timestamped:
            return prefix + timestamped.group(1)
    return None


def export(cache, classpath, catalog, destination):
    cache, destination = Path(cache), Path(destination)
    provided = jvm.read_catalogs([{"catalog": str(catalog)}])
    records = {}
    for path in sorted(cache.rglob("*.pom")):
        primary = path.parent / f"{path.parent.parent.name}-{path.parent.name}.pom"
        if primary.is_file() and path != primary:
            continue
        if path.name.endswith('-build.pom') and path.with_name(path.name[:-len('-build.pom')]+'.pom').is_file():
            continue
        try:
            root, properties, coordinate = jvm.pom_model(path)
        except (ValueError, ET.ParseError):
            continue
        records.setdefault(coordinate, (path, root, properties))
    properties_cache = {}
    copying = set()

    def directory(coordinate):
        jvm.validate_coordinates(*coordinate)
        group, name, version = coordinate
        return destination / group.replace(".", "/") / name / version

    def metadata(coordinate):
        if coordinate in properties_cache:
            return properties_cache[coordinate]
        if coordinate in copying:
            raise ValueError(f"cyclic Maven metadata in native classpath: {coordinate}")
        if coordinate[:2] in provided:
            return {}
        if coordinate not in records:
            raise ValueError(f"native classpath needs a Maven metadata projection: {coordinate}")
        copying.add(coordinate)
        path, root, properties = records[coordinate]
        parent = jvm.child(root, "parent")
        inherited = {}
        if parent is not None:
            identity = tuple(jvm.expand(jvm.text(parent, field), properties)
                             for field in ("groupId", "artifactId", "version"))
            inherited = metadata(identity)
        effective = {**inherited, **properties}
        management = jvm.child(root, "dependencyManagement")
        dependencies = jvm.child(management, "dependencies") if management is not None else None
        for dependency in dependencies if dependencies is not None else []:
            if jvm.text(dependency, "scope") == "import" and jvm.text(dependency, "type") == "pom":
                identity = tuple(jvm.expand(jvm.text(dependency, field), effective)
                                 for field in ("groupId", "artifactId", "version"))
                metadata(identity)
        target = directory(coordinate)
        name, version = coordinate[1:]
        jvm.write(target / f"{name}-{version}.pom", path.read_bytes())
        build = path.with_name(path.stem + "-build.pom")
        if build.is_file():
            jvm.write(target / f"{name}-{version}-build.pom", build.read_bytes())
        richer = path.with_suffix(".module")
        if richer.is_file():
            jvm.write(target / f"{name}-{version}.module", richer.read_bytes())
        copying.remove(coordinate)
        properties_cache[coordinate] = effective
        return effective

    values = classpath.values() if isinstance(classpath, dict) else [classpath]
    selected = {}
    for paths in values:
        for reference in paths:
            path = Path(re.sub(r"^q?ref:[^:]*:[^:]*:", "", reference))
            if not path.is_file():
                raise ValueError(f"native runtime classpath entry is missing: {path}")
            matches = []
            primary = path.parent / f"{path.parent.parent.name}-{path.parent.name}.pom"
            poms = [primary] if primary.is_file() else sorted(path.parent.glob("*.pom"))
            for pom in poms:
                root, properties, coordinate = jvm.pom_model(pom)
                name, version = coordinate[1:]
                if version.endswith('-SNAPSHOT') and artifact_name(pom, coordinate) != unquote(pom.name):
                    stem, filename = unquote(pom.stem), unquote(path.name)
                    if not (filename.startswith(stem + '.') or filename.startswith(stem + '-')):
                        continue
                if artifact_name(path, coordinate) is not None:
                    matches.append(coordinate)
                    records[coordinate] = pom, root, properties
            if len(matches) != 1:
                raise ValueError(f"native runtime artifact needs a Maven metadata projection: {path}")
            coordinate = matches[0]
            group, name, version = coordinate
            module = provided.get((group, name))
            actual = module["version"] if module is not None else version
            previous = selected.setdefault((group, name), actual)
            if previous != actual:
                raise ValueError(f"conflicting selected native runtime versions for {group}:{name}")
            if module is None:
                metadata(coordinate)
                jvm.write(directory(coordinate) / artifact_name(path, coordinate), path.read_bytes())
    modules = [{"group": group, "name": name, "version": version}
               for (group, name), version in sorted(selected.items())]
    jvm.write(destination / "purifier-runtime-modules.json",
              (json.dumps({"modules": modules}, sort_keys=True) + "\n").encode())


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("cache", "classpath", "catalog", "destination"):
        parser.add_argument("--" + option, type=Path, required=True)
    args = parser.parse_args()
    try:
        export(args.cache, json.loads(args.classpath.read_text()), args.catalog, args.destination)
    except (ValueError, KeyError, ET.ParseError) as error:
        parser.exit(1, f"purifier: {error}\n")


if __name__ == "__main__":
    main()
