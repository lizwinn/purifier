# SPDX-License-Identifier: MIT
# Build a native RubyGems repository using the target SDK's own package codecs.
require "rubygems/package"
require "json"
require "fileutils"
require "tmpdir"
require "zlib"
require "digest"
require "base64"

specification = JSON.parse(File.read(ARGV.fetch(0)))
destination = ARGV.fetch(1)
catalog_path = ARGV.fetch(2)
FileUtils.mkdir_p(File.join(destination, "gems"))
FileUtils.mkdir_p(File.join(destination, "quick", "Marshal.4.8"))
intrinsics = {}

def add_intrinsic(intrinsics, entry)
  name = entry.fetch("name")
  raise "conflicting Ruby SDK inputs for #{name}" if intrinsics.key?(name) && intrinsics[name] != entry
  intrinsics[name] = entry
end
specification.fetch("intrinsics", []).each { |entry| add_intrinsic(intrinsics, entry) }
packages = specification.fetch("archives").map do |archive|
  [archive, Gem::Package.new(archive).spec, nil]
end
specification.fetch("dependencies", []).each do |input|
  catalog = JSON.parse(File.read(input.fetch("catalog")))
  catalog.fetch("intrinsics", []).each { |entry| add_intrinsic(intrinsics, entry) }
  catalog.fetch("packages").each do |entry|
    archive = File.join(input.fetch("repository"), entry.fetch("archive"))
    actual = "sha512-#{Base64.strict_encode64(Digest::SHA512.file(archive).digest)}"
    raise "RubyGems input archive integrity mismatch: #{archive}" unless actual == entry.fetch("integrity")
    gem_spec = Gem::Package.new(archive).spec
    unless [gem_spec.name, gem_spec.version.to_s, gem_spec.platform.to_s] == entry.values_at("name", "version", "platform")
      raise "RubyGems input catalog coordinates do not match #{archive}"
    end
    packages << [archive, gem_spec, entry]
  end
end
packages = packages.uniq { |archive, spec, _| [spec.name, spec.version, Digest::SHA512.file(archive).hexdigest] }
if (roots = specification["roots"])
  candidates = packages.group_by { |_, spec, _| spec.name }
  selected = {}
  visit = lambda do |name|
    return if selected.key?(name)
    if intrinsics.key?(name)
      selected[name] = true
      intrinsics.fetch(name).fetch("dependencies", []).each do |dependency|
        child = dependency.fetch("name")
        visit.call(child) if candidates.key?(child) || intrinsics.key?(child)
      end
      next
    end
    raise "missing RubyGems projection root #{name}" unless candidates.key?(name)
    selected[name] = true
    candidates.fetch(name).each do |_, spec, _|
      spec.runtime_dependencies.each { |dependency| visit.call(dependency.name) if candidates.key?(dependency.name) || intrinsics.key?(dependency.name) }
    end
  end
  roots.each { |name| visit.call(name) }
  packages.select! { |_, spec, _| selected.key?(spec.name) }
  intrinsics.select! { |name, _| selected.key?(name) }
end
duplicates = packages.group_by { |_, spec| spec.name }.select { |_, values| values.size > 1 }
raise "multiple gem versions need an explicit projection root: #{duplicates.keys.join(', ')}" unless duplicates.empty?
suffix = specification.fetch("identity").gsub(/[^a-zA-Z0-9]/, "").downcase
raise "missing Nix producer identity" if suffix.empty?
# Hash the reachable native graph, so independent producers can share an
# identical prepared dependency without coupling its version to unrelated roots.
raise "Ruby SDK dependency is also supplied as an independent gem" if packages.any? { |_, spec, _| intrinsics.key?(spec.name) }
by_name = packages.to_h { |archive, spec, provided| [spec.name, [archive, spec, provided]] }
versions = packages.to_h do |archive, spec, provided|
  if provided
    [spec.name, spec.version.to_s]
  else
    reachable = {}
    visit = lambda do |name|
      return if reachable.key?(name)
      if intrinsics.key?(name)
        entry = intrinsics.fetch(name)
        reachable[name] = [nil, nil, entry]
        entry.fetch("dependencies", []).each do |dependency|
          child = dependency.fetch("name")
          visit.call(child) if by_name.key?(child) || intrinsics.key?(child)
        end
      else
        item = by_name.fetch(name)
        reachable[name] = item
        item[1].runtime_dependencies.each { |dependency| visit.call(dependency.name) if by_name.key?(dependency.name) || intrinsics.key?(dependency.name) }
      end
    end
    visit.call(spec.name)
    graph = reachable.keys.sort.map do |name|
      item_archive, item_spec, item_provided = reachable.fetch(name)
      next [name, "ruby-sdk", item_provided] unless item_spec
      seed = item_provided ? item_provided.fetch("integrity") : specification.fetch("archiveIdentities", {}).fetch(item_archive, suffix)
      [name, item_spec.version.to_s, item_spec.platform.to_s, seed, item_spec.runtime_dependencies.map { |dependency| [dependency.name, dependency.requirement.to_s] }.sort]
    end
    sdk_dependencies = reachable.values.filter_map { |_, value, provided| provided unless value }
    fingerprint = Digest::SHA256.hexdigest(JSON.dump([specification.fetch("projectorIdentity", "rubygems-v1"), graph, sdk_dependencies]))
    [spec.name, "#{spec.version}.1.nix.h#{fingerprint}"]
  end
end

intrinsics.each { |name, entry| versions[name] = entry.fetch("version") }
entries = []
packages.each do |archive, original, provided|
  Dir.mktmpdir("rubygems-projection-") do |directory|
    package = Gem::Package.new(archive)
    package.extract_files(directory) unless provided
    spec = original.dup
    spec.version = versions.fetch(original.name)
    spec.dependencies.each do |dependency|
      next if provided
      next unless dependency.type == :runtime && versions.key?(dependency.name)
      dependency.instance_variable_set(:@requirement, Gem::Requirement.new("= #{versions.fetch(dependency.name)}"))
    end
    unless provided
      seen_sdk = {}
      pin_sdk_children = lambda do |name|
        return if seen_sdk[name] || !intrinsics.key?(name)
        seen_sdk[name] = true
        intrinsics.fetch(name).fetch("dependencies", []).each do |dependency|
          child = dependency.fetch("name")
          if versions.key?(child)
            requirement = Gem::Requirement.new(dependency.fetch("requirement"))
            unless requirement.satisfied_by?(Gem::Version.new(versions.fetch(child)))
              raise "Ruby SDK dependency #{name} requires #{child} #{requirement}, incompatible Nix input #{versions.fetch(child)}"
            end
            spec.add_runtime_dependency(child, "= #{versions.fetch(child)}") unless spec.runtime_dependencies.any? { |value| value.name == child }
            pin_sdk_children.call(child)
          end
        end
      end
      spec.runtime_dependencies.map(&:name).each { |name| pin_sdk_children.call(name) }
    end
    # Original signatures describe different bytes after metadata projection.
    spec.cert_chain = [] unless provided
    filename = provided ? File.basename(archive) : Dir.chdir(directory) { Gem::Package.build(spec) }
    source_archive = provided ? archive : File.join(directory, filename)
    FileUtils.cp(source_archive, File.join(destination, "gems", filename))
    quick = File.join(destination, "quick", "Marshal.4.8", "#{spec.original_name}.gemspec.rz")
    File.binwrite(quick, Zlib::Deflate.deflate(Marshal.dump(spec)))
    entries << { "name" => spec.name, "version" => spec.version.to_s,
                 "sourceVersion" => provided ? provided.fetch("sourceVersion") : original.version.to_s, "platform" => spec.platform.to_s,
                 "integrity" => "sha512-#{Base64.strict_encode64(Digest::SHA512.file(source_archive).digest)}",
                 "archive" => "gems/#{filename}" }
  end
end
specs = entries.map { |entry| [entry.fetch("name"), Gem::Version.new(entry.fetch("version")), entry.fetch("platform")] }.sort
{
  "specs" => specs.reject { |_, version, _| version.prerelease? },
  "latest_specs" => specs.reject { |_, version, _| version.prerelease? }.group_by { |name, _, platform| [name, platform] }.values.map { |values| values.max_by { |_, version, _| version } }.sort,
  "prerelease_specs" => specs.select { |_, version, _| version.prerelease? },
}.each do |kind, values|
  Zlib::GzipWriter.open(File.join(destination, "#{kind}.4.8.gz")) do |writer|
    writer.mtime = 0
    writer.write(Marshal.dump(values))
  end
end
FileUtils.mkdir_p(File.dirname(catalog_path))
File.write(catalog_path, JSON.pretty_generate({ "format" => "rubygems", "packages" => entries, "intrinsics" => intrinsics.values.sort_by { |entry| entry.fetch("name") } }) + "\n")
