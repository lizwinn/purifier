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
    synthetic = {}
    properties_cache = {}
    copying = set()

    def gradle_model(path):
        for descriptor in sorted(path.parent.glob('*.module')):
            document = json.loads(descriptor.read_text())
            coordinate = (document.get('component', {}).get('group'),
                          unquote(path.parent.parent.name), unquote(path.parent.name))
            if not coordinate[0] or artifact_name(path, coordinate) is None:
                continue
            candidates = []
            for variant in document.get('variants', []):
                attributes = variant.get('attributes', {})
                if (attributes.get('org.gradle.category', 'library') != 'library'
                        or attributes.get('org.jetbrains.kotlin.platform.type', 'jvm') != 'jvm'
                        or attributes.get('org.gradle.usage') not in {'java-api', 'java-runtime'}):
                    continue
                if any(artifact_name(Path(file.get('name', '')), coordinate) == artifact_name(path, coordinate)
                       for file in variant.get('files', [])):
                    candidates.append(variant)
            runtime = [variant for variant in candidates
                       if variant.get('attributes', {}).get('org.gradle.usage') == 'java-runtime'] or candidates
            shapes = {json.dumps({field: variant.get(field, [])
                                   for field in ('dependencies', 'dependencyConstraints')}, sort_keys=True)
                      for variant in runtime}
            if len(shapes) != 1:
                raise ValueError('Gradle metadata needs an unambiguous native JVM runtime variant: '+str(descriptor))
            root = ET.Element('{'+jvm.POM_NS+'}project')
            jvm.put(root, 'modelVersion', '4.0.0')
            root.append(ET.Comment(' do_not_remove: published-with-gradle-metadata '))
            for field, value in zip(('groupId', 'artifactId', 'version'), coordinate):
                jvm.put(root, field, value)
            synthetic[coordinate] = {**document, 'variants': candidates}, runtime[0]
            records[coordinate] = descriptor, root, {}
            return coordinate
        return None

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
        if coordinate in synthetic:
            jvm.write(target / f"{name}-{version}.pom", jvm.xml_bytes(root))
        else:
            jvm.write(target / f"{name}-{version}.pom", path.read_bytes())
        build = path.with_name(path.stem + "-build.pom")
        if build.is_file():
            jvm.write(target / f"{name}-{version}-build.pom", build.read_bytes())
        richer = path.with_suffix(".module")
        if coordinate in synthetic:
            document, _ = synthetic[coordinate]
            jvm.write(target / f"{name}-{version}.module", (json.dumps(document, indent=2)+'\n').encode())
        elif richer.is_file():
            jvm.write(target / f"{name}-{version}.module", richer.read_bytes())
        copying.remove(coordinate)
        properties_cache[coordinate] = effective
        return effective

    values = classpath.values() if isinstance(classpath, dict) else [classpath]
    selected = {}
    artifacts = []
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
            if not matches:
                coordinate = gradle_model(path)
                if coordinate is not None:
                    matches.append(coordinate)
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
                artifacts.append((path, coordinate))

    # Resolve synthesized POM dependency versions from the complete native
    # classpath, after every selected artifact has been identified.
    for coordinate, (_, variant) in synthetic.items():
        _, root, _ = records[coordinate]
        for field, parent in [('dependencies', root), ('dependencyConstraints', None)]:
            declarations = variant.get(field, [])
            if not declarations:
                continue
            if parent is None:
                parent = ET.SubElement(root, '{'+jvm.POM_NS+'}dependencyManagement')
            dependencies = ET.SubElement(parent, '{'+jvm.POM_NS+'}dependencies')
            for declaration in declarations:
                version = declaration.get('version', {})
                if isinstance(version, dict):
                    version = version.get('strictly', version.get('requires', version.get('prefers')))
                key = declaration['group'], declaration['module']
                version = selected.get(key, version)
                if not version:
                    raise ValueError('Gradle dependency has no selected Maven version: '+str(key))
                dependency = ET.SubElement(dependencies, '{'+jvm.POM_NS+'}dependency')
                for name, value in zip(('groupId', 'artifactId', 'version'), (*key, version)):
                    jvm.put(dependency, name, value)
                if declaration.get('excludes'):
                    exclusions = ET.SubElement(dependency, '{'+jvm.POM_NS+'}exclusions')
                    for exclude in declaration['excludes']:
                        exclusion = ET.SubElement(exclusions, '{'+jvm.POM_NS+'}exclusion')
                        jvm.put(exclusion, 'groupId', exclude.get('group', '*'))
                        jvm.put(exclusion, 'artifactId', exclude.get('module', '*'))
    for path, coordinate in artifacts:
        metadata(coordinate)
        jvm.write(directory(coordinate) / artifact_name(path, coordinate), path.read_bytes())
    modules = [{"group": group, "name": name, "version": version,
                **({'metadataProjection': True} if (group, name, version) in synthetic else {})}
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
