# SPDX-License-Identifier: MIT
"""Native repository views of Nix-built JVM components.

The Nix caller supplies build identities and repositories, never version rules.
The catalog is outside the native repository so recorders cannot mistake it
for a downloaded artifact.
"""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
import xml.etree.ElementTree as ET

POM_NS = "http://maven.apache.org/POM/4.0.0"
ET.register_namespace("", POM_NS)


def local(element):
    return element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""


def child(element, name):
    return next((e for e in element if local(e) == name), None)


def text(element, name, default=""):
    found = child(element, name)
    return found.text.strip() if found is not None and found.text else default


def put(element, name, value):
    found = child(element, name)
    if found is None:
        namespace = element.tag.rsplit("}", 1)[0] + "}" if "}" in element.tag else ""
        found = ET.SubElement(element, namespace + name)
    found.text = value


def expand(value, properties):
    for _ in range(32):
        previous = value
        for key, replacement in properties.items():
            value = value.replace("${" + key + "}", replacement)
        if value == previous:
            break
    return value


def validate_coordinates(group, name, version):
    for value in (group, name, version):
        if (not isinstance(value, str) or not value or value in {".", ".."}
                or any(character in value for character in ("/", "\\", ":"))
                or any(ord(character) < 32 or ord(character) == 127 for character in value)):
            raise ValueError(f"invalid published JVM coordinate: {group!r}:{name!r}:{version!r}")
    if any(not segment for segment in group.split(".")):
        raise ValueError(f"invalid published JVM group: {group!r}")


def pom_model(path):
    root = ET.parse(path, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))).getroot()
    parent = child(root, "parent")
    group = text(root, "groupId") or (text(parent, "groupId") if parent is not None else "")
    version = text(root, "version") or (text(parent, "version") if parent is not None else "")
    properties = {"project.groupId": group, "pom.groupId": group,
                  "project.version": version, "pom.version": version}
    props = child(root, "properties")
    if props is not None:
        properties.update({local(e): e.text or "" for e in props if isinstance(e.tag, str)})
    group = expand(group, properties)
    name = expand(text(root, "artifactId"), properties)
    version = expand(version, properties)
    if not all((group, name, version)) or any("${" in s for s in (group, name, version)):
        raise ValueError(f"cannot determine published coordinates of {path}")
    validate_coordinates(group, name, version)
    return root, properties, (group, name, version)


def key(module):
    return module["group"], module["name"]


def read_catalogs(inputs):
    modules = {}
    for source in inputs:
        catalog = json.loads(Path(source["catalog"]).read_text())
        if catalog.get("version") != 1:
            raise ValueError("unsupported JVM projection catalog version")
        for module in catalog["modules"]:
            validate_coordinates(module["group"], module["name"], module["version"])
            existing = modules.get(key(module))
            if existing is not None and existing != module:
                raise ValueError(f"conflicting Nix providers for {module['group']}:{module['name']}")
            modules[key(module)] = module
    return modules


def rewrite_pom(root, properties, modules, own=None):
    if own is not None:
        put(root, "version", own["version"])
    for element in root.iter():
        if local(element) not in {"dependency", "parent", "plugin", "extension"}:
            continue
        group = expand(text(element, "groupId"), properties)
        name = expand(text(element, "artifactId"), properties)
        selected = modules.get((group, name))
        if selected is not None:
            put(element, "version", selected["version"])
            if local(element) == "parent":
                # Published parents are resolved from the repository, not a
                # relative source-tree POM bearing the unprojected version.
                put(element, "relativePath", "")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def renamed(filename, name, old, new):
    prefix = name + "-" + old
    if not filename.startswith(prefix):
        raise ValueError(f"artifact {filename} does not match {name}:{old}")
    return name + "-" + new + filename[len(prefix):]


def module_metadata(path, module, modules):
    document = json.loads(path.read_text())
    document["component"]["version"] = module["version"]
    for variant in document.get("variants", []):
        for dependency in variant.get("dependencies", []) + variant.get("dependencyConstraints", []):
            selected = modules.get((dependency.get("group"), dependency.get("module")))
            if selected:
                # A single exact selection replaces conflicting rich constraints.
                dependency["version"] = {"requires": selected["version"]}
        for artifact in variant.get("files", []):
            for field in ("name", "url"):
                if field in artifact:
                    artifact[field] = renamed(artifact[field], module["name"],
                                              module["originalVersion"], module["version"])
        available = variant.get("available-at")
        if available:
            selected = modules.get((available.get("group"), available.get("module")))
            if selected:
                old = available["version"]
                available["version"] = selected["version"]
                available["url"] = available["url"].replace("/" + old + "/", "/" + selected["version"] + "/")
                available["url"] = available["url"].replace("-" + old + ".", "-" + selected["version"] + ".")
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()


def write(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != contents:
            raise ValueError(f"conflicting artifact bytes at {path}")
        return
    path.write_bytes(contents)


def merge(source, target):
    for path in sorted(Path(source).rglob("*")):
        if path.is_file():
            write(target / path.relative_to(source), path.read_bytes())


def checksum_repository(repository):
    for path in sorted(repository.rglob("*")):
        if not path.is_file() or path.suffix in {".md5", ".sha1", ".sha256", ".sha512", ".asc"}:
            continue
        for algorithm in ("md5", "sha1", "sha256", "sha512"):
            write(Path(str(path) + "." + algorithm),
                  (hashlib.new(algorithm, path.read_bytes()).hexdigest() + "\n").encode())


def ivy_to_pom(path):
    root = ET.parse(path).getroot()
    info = child(root, "info")
    if info is None:
        raise ValueError(f"Ivy descriptor has no info: {path}")
    # Extra attributes select distinct modules in Ivy; silently dropping them
    # would conflate e.g. sbt plugins for different Scala/sbt versions.
    if any("}" in attribute for attribute in info.attrib):
        raise ValueError(f"Ivy extra attributes need an explicit variant projection: {path}")
    pom = ET.Element("{" + POM_NS + "}project")
    for name, value in (("modelVersion", "4.0.0"), ("groupId", info.get("organisation", "")),
                        ("artifactId", info.get("module", "")), ("version", info.get("revision", ""))):
        put(pom, name, value)
    configurations = child(root, "configurations")
    private = {conf.get("name") for conf in configurations
               if conf.get("visibility", "public") == "private"} if configurations is not None else set()
    dependencies = child(root, "dependencies")
    if dependencies is not None:
        target = ET.SubElement(pom, "{" + POM_NS + "}dependencies")
        for dependency in dependencies:
            if local(dependency) != "dependency":
                continue
            conf = dependency.get("conf", "compile->default")
            mappings = conf.split(";")
            public_mappings = [mapping for mapping in mappings
                               if any(name.strip() not in private
                                      for name in mapping.split("->")[0].split(","))]
            if not public_mappings:
                continue
            conf = ";".join(public_mappings)
            # sbt uses Ivy's fallback syntax for ordinary JVM scopes.
            scopes = []
            for mapping in public_mappings:
                source, separator, destination = mapping.partition("->")
                if not separator or destination.strip() not in {
                    "default", "compile", "runtime", "default(compile)", "default(runtime)"
                }:
                    raise ValueError(f"Ivy configuration cannot be represented faithfully in a POM: {conf}")
                for scope in source.split(","):
                    scope = scope.strip()
                    if scope in private:
                        continue
                    if scope in {"*", "default"}:
                        scope = "compile"
                    if scope not in {"compile", "runtime", "test", "provided"}:
                        raise ValueError(f"Ivy configuration cannot be represented faithfully in a POM: {conf}")
                    scopes.append(scope)
            if "provided" in scopes and any(scope not in {"provided", "test"} for scope in scopes):
                raise ValueError(f"mixed Ivy provided configuration needs a richer Maven projection: {conf}")
            scope = next(value for value in ("compile", "provided", "runtime", "test") if value in scopes)
            node = ET.SubElement(target, "{" + POM_NS + "}dependency")
            for name, value in (("groupId", dependency.get("org", "")),
                                ("artifactId", dependency.get("name", "")), ("version", dependency.get("rev", ""))):
                put(node, name, value)
            if scope != "compile":
                put(node, "scope", scope)
            exclusions = []
            for selection in dependency:
                if local(selection) != "exclude" or any(
                    selection.get(key, "*") != "*" for key in ("name", "type", "ext")
                ) or selection.get("matcher", "exact") not in {"exact", "exactOrRegexp"}:
                    raise ValueError(f"Ivy artifact selection needs a richer Maven projection: {path}")
                group, name = selection.get("org", "*"), selection.get("module", "*")
                if any(character in group + name for character in "[]()|+?\\"):
                    raise ValueError(f"Ivy exclusion pattern cannot be represented in a POM: {path}")
                if any("*" in value and value != "*" for value in (group, name)):
                    raise ValueError(f"Ivy exclusion pattern cannot be represented in a POM: {path}")
                exclusions.append((group, name))
            if dependency.get("transitive", "true") != "true":
                exclusions = [("*", "*")]
            if exclusions:
                target_exclusions = ET.SubElement(node, "{" + POM_NS + "}exclusions")
                for group, name in sorted(set(exclusions)):
                    exclusion = ET.SubElement(target_exclusions, "{" + POM_NS + "}exclusion")
                    put(exclusion, "groupId", group)
                    put(exclusion, "artifactId", name)
    coordinates = (info.get("organisation"), info.get("module"), info.get("revision"))
    validate_coordinates(*coordinates)
    return pom, {}, coordinates


def add_runtime(repository, modules, owned, maven):
    source = Path(repository)
    graph = json.loads((source / "purifier-runtime-modules.json").read_text())
    owned_keys = {key(entry[-1]) for entry in owned}
    owned_directories = {Path(module["group"].replace(".", "/")) / module["name"] / module["originalVersion"]
                         for *_, module in owned}
    runtime = []
    for selected in graph["modules"]:
        validate_coordinates(selected["group"], selected["name"], selected["version"])
        coordinate = key(selected)
        if coordinate in owned_keys:
            continue
        existing = modules.get(coordinate)
        if existing is not None:
            if existing["version"] != selected["version"]:
                raise ValueError(f"runtime graph disagrees with Nix input for {coordinate}")
            continue
        directory = source / selected["group"].replace(".", "/") / selected["name"] / selected["version"]
        if not directory.is_dir():
            raise ValueError(f"runtime component needs an Ivy-to-Maven projection: {coordinate}")
        files = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(directory.iterdir())
                 if path.is_file() and path.suffix not in {".md5", ".sha1", ".sha256", ".sha512", ".asc"}}
        # An unchanged published component keeps its native version. Its
        # identity belongs to its bytes, never to the project re-exporting it.
        modules[coordinate] = {**selected, "originalVersion": selected["version"],
                               "producer": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}
        pom = directory / f"{selected['name']}-{selected['version']}.pom"
        if pom.is_file():
            root, properties, _ = pom_model(pom)
            runtime.append((pom, "maven", root, properties, modules[coordinate]))
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if not path.is_file() or path.name in {"sbt-nix-repository.json", "purifier-runtime-modules.json"}:
            continue
        if path.suffix in {".md5", ".sha1", ".sha256", ".sha512", ".asc"}:
            continue
        if any(directory == relative.parent for directory in owned_directories):
            continue
        write(maven / relative, path.read_bytes())
    return runtime


def normalize_runtime(runtime, modules, maven):
    """Reproject foreign descriptors when Nix changes their dependency graph.

    Never modify a third-party POM under its original published coordinates.
    Hash the affected component's selected closure, rather than the exporting
    root: equivalent diamonds keep equivalent component identities.
    """
    edges = {}
    affected = set()
    records = {key(entry[-1]): entry for entry in runtime}
    for coordinate, (_, _, root, properties, _) in records.items():
        edges[coordinate] = set()
        for element in root.iter():
            if local(element) not in {"dependency", "parent", "plugin", "extension"}:
                continue
            target = (expand(text(element, "groupId"), properties),
                      expand(text(element, "artifactId"), properties))
            selected = modules.get(target)
            if selected is not None:
                edges[coordinate].add(target)
                if expand(text(element, "version"), properties) != selected["version"]:
                    affected.add(coordinate)
    while True:
        additional = {coordinate for coordinate, targets in edges.items() if targets & affected} - affected
        if not additional:
            break
        affected.update(additional)
    baseline = {coordinate: dict(module) for coordinate, module in modules.items()}
    for coordinate in sorted(affected):
        visited = set()
        def visit(node):
            if node in visited:
                return
            visited.add(node)
            for target in edges.get(node, set()):
                visit(target)
        visit(coordinate)
        fingerprint = {"component": baseline[coordinate],
                       "closure": [baseline[node] for node in sorted(visited)]}
        identity = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
        module = modules[coordinate]
        module["sourceProducer"] = module["producer"]
        module["producer"] = identity
        module["version"] = module["originalVersion"].removesuffix("-SNAPSHOT") + "-nix-" + identity
        original = maven / module["group"].replace(".", "/") / module["name"] / module["originalVersion"]
        if original.is_dir():
            shutil.rmtree(original)
    return [records[coordinate] for coordinate in sorted(affected)]


def project(spec, maven, ivy, metadata):
    source = Path(spec["repository"]) if spec.get("repository") is not None else None
    maven.mkdir(parents=True, exist_ok=True)
    ivy.mkdir(parents=True, exist_ok=True)
    modules = read_catalogs(spec.get("dependencies", []))
    owned = []
    for path in sorted(source.rglob("*")) if source is not None else []:
        if "nix-support" in path.relative_to(source).parts or not path.is_file():
            continue
        if path.suffix == ".pom":
            root, properties, coordinates = pom_model(path)
            layout = "maven"
        elif path.name == "ivy.xml" or (path.name.startswith("ivy-") and path.suffix == ".xml"):
            root, properties, coordinates = ivy_to_pom(path)
            layout = "ivy"
        else:
            continue
        group, name, version = coordinates
        module = {"group": group, "name": name, "originalVersion": version,
                  "version": version.removesuffix("-SNAPSHOT") + "-nix-" + spec["producerHash"],
                  "producer": spec["producerHash"]}
        validate_coordinates(group, name, module["version"])
        existing = modules.get(key(module))
        if existing is not None and existing != module:
            raise ValueError(f"conflicting Nix providers for {group}:{name}")
        modules[key(module)] = module
        owned.append((path, layout, root, properties, module))
    if source is not None and not owned:
        raise ValueError(f"no published JVM components in {source}")
    if spec.get("runtimeRepository"):
        runtime = add_runtime(spec["runtimeRepository"], modules, owned, maven)
        owned.extend(normalize_runtime(runtime, modules, maven))
    for dependency in spec.get("dependencies", []):
        merge(dependency["maven"], maven)
        if "ivy" in dependency:
            merge(dependency["ivy"], ivy)
    for path, layout, root, properties, module in owned:
        group, name = key(module)
        old, version = module["originalVersion"], module["version"]
        target = maven / group.replace(".", "/") / name / version
        write(target / f"{name}-{version}.pom", rewrite_pom(root, properties, modules, module))
        if layout == "maven":
            for artifact in sorted(path.parent.iterdir()):
                if not artifact.is_file() or artifact.suffix in {".pom", ".md5", ".sha1", ".sha256", ".sha512", ".asc"}:
                    continue
                if not artifact.name.startswith(name + "-" + old):
                    continue
                contents = module_metadata(artifact, module, modules) if artifact.suffix == ".module" else artifact.read_bytes()
                write(target / renamed(artifact.name, name, old, version), contents)
        else:
            # Standard sbt/Ivy layout: org/module/revision/{ivys,jars,...}.
            for artifact in sorted(path.parent.parent.rglob("*")):
                if artifact.is_file() and artifact.suffix in {".jar", ".zip"}:
                    classifier = ""
                    if artifact.parent.name in {"srcs", "sources"}:
                        classifier = "-sources"
                    elif artifact.parent.name == "docs":
                        classifier = "-javadoc"
                    elif artifact.parent.name != "jars":
                        raise ValueError(f"unsupported Ivy artifact type: {artifact}")
                    write(target / f"{name}-{version}{classifier}{artifact.suffix}", artifact.read_bytes())
        # The Maven-compatible Ivy view uses the same unique module identity.
        # Maven resolvers can consume the Maven view; Ivy clients retain their
        # native descriptor and artifact layout when the producer used Ivy.
        if layout == "ivy":
            original = ET.parse(path).getroot()
            child(original, "info").set("revision", version)
            child(original, "info").set("publication", "19800101000000")
            for dependency in original.iter("dependency"):
                selected = modules.get((dependency.get("org"), dependency.get("name")))
                if selected:
                    dependency.set("rev", selected["version"])
            ivy_target = ivy / group / name / version
            for artifact in sorted(path.parent.parent.rglob("*")):
                if artifact.is_file() and artifact.suffix not in {".xml", ".md5", ".sha1", ".sha256", ".sha512", ".asc"}:
                    relative = artifact.relative_to(path.parent.parent)
                    filename = relative.name.replace("-" + old, "-" + version)
                    write(ivy_target / relative.parent / filename, artifact.read_bytes())
            write(ivy_target / "ivys" / "ivy.xml", ET.tostring(original, encoding="utf-8", xml_declaration=True))
    checksum_repository(maven)
    checksum_repository(ivy)
    metadata.mkdir(parents=True, exist_ok=True)
    write(metadata / "catalog.json", (json.dumps({"version": 1, "producer": spec.get("producerHash"),
                                                 "modules": sorted(modules.values(), key=key)},
                                                indent=2, sort_keys=True) + "\n").encode())


def rewrite_sources(root, catalog):
    modules = read_catalogs([{"catalog": str(catalog)}])
    for path in sorted(Path(root).rglob("pom.xml")):
        if any(part in {"target", ".git", ".maven-purifier"} for part in path.relative_to(root).parts):
            continue
        model, properties, _ = pom_model(path)
        path.write_bytes(rewrite_pom(model, properties, modules))


def clojure_config(catalog):
    modules = read_catalogs([{"catalog": str(catalog)}])
    coordinates = []
    for (group, name), module in sorted(modules.items()):
        if not all(re.fullmatch(r"[A-Za-z0-9_.+\-]+", part) for part in (group, name)):
            raise ValueError(f"coordinate cannot be represented as an EDN symbol: {group}:{name}")
        coordinates.append(f"{group}/{name} {{:mvn/version {json.dumps(module['version'])}}}")
    return "{:aliases {:nix-purifier-project-inputs {:override-deps {" + " ".join(coordinates) + "}}}}\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clojure-config", action="store_true")
    parser.add_argument("--rewrite-pom", type=Path)
    parser.add_argument("--rewrite-sources", type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--maven", type=Path)
    parser.add_argument("--ivy", type=Path)
    parser.add_argument("--metadata", type=Path)
    args = parser.parse_args()
    try:
        if args.clojure_config:
            if args.catalog is None:
                parser.error("--clojure-config requires --catalog")
            print(clojure_config(args.catalog), end="")
        elif args.rewrite_pom is not None:
            if args.catalog is None:
                parser.error("--rewrite-pom requires --catalog")
            model, properties, _ = pom_model(args.rewrite_pom)
            modules = read_catalogs([{"catalog": str(args.catalog)}])
            args.rewrite_pom.write_bytes(rewrite_pom(model, properties, modules))
        elif args.rewrite_sources is not None:
            if args.catalog is None:
                parser.error("--rewrite-sources requires --catalog")
            rewrite_sources(args.rewrite_sources, args.catalog)
        elif all((args.spec, args.maven, args.ivy, args.metadata)):
            project(json.loads(args.spec.read_text()), args.maven, args.ivy, args.metadata)
        else:
            parser.error("projection requires --spec, --maven, --ivy and --metadata")
    except (ValueError, KeyError, ET.ParseError) as error:
        parser.exit(1, f"purifier: {error}\n")


if __name__ == "__main__":
    main()
