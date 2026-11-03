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
packages = specification.fetch("archives").map do |archive|
  [archive, Gem::Package.new(archive).spec, nil]
end
specification.fetch("dependencies", []).each do |input|
  JSON.parse(File.read(input.fetch("catalog"))).fetch("packages").each do |entry|
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
duplicates = packages.group_by { |_, spec| spec.name }.select { |_, values| values.size > 1 }
raise "multiple gem versions need an explicit projection root: #{duplicates.keys.join(', ')}" unless duplicates.empty?
suffix = specification.fetch("identity").gsub(/[^a-zA-Z0-9]/, "").downcase
raise "missing Nix producer identity" if suffix.empty?
# Hash the reachable native graph, so independent producers can share an
# identical prepared dependency without coupling its version to unrelated roots.
by_name = packages.to_h { |archive, spec, provided| [spec.name, [archive, spec, provided]] }
versions = packages.to_h do |archive, spec, provided|
  if provided
    [spec.name, spec.version.to_s]
  else
    reachable = {}
    visit = lambda do |name|
      return if reachable.key?(name)
      item = by_name.fetch(name)
      reachable[name] = item
      item[1].runtime_dependencies.each { |dependency| visit.call(dependency.name) if by_name.key?(dependency.name) }
    end
    visit.call(spec.name)
    graph = reachable.keys.sort.map do |name|
      item_archive, item_spec, item_provided = reachable.fetch(name)
      seed = item_provided ? item_provided.fetch("integrity") : specification.fetch("archiveIdentities", {}).fetch(item_archive, suffix)
      [name, item_spec.version.to_s, item_spec.platform.to_s, seed, item_spec.runtime_dependencies.map { |dependency| [dependency.name, dependency.requirement.to_s] }.sort]
    end
    fingerprint = Digest::SHA256.hexdigest(JSON.dump([specification.fetch("projectorIdentity", "rubygems-v1"), graph]))
    [spec.name, "#{spec.version}.nix.h#{fingerprint}"]
  end
end

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
  Zlib::GzipWriter.open(File.join(destination, "#{kind}.4.8.gz")) { |writer| writer.write(Marshal.dump(values)) }
end
FileUtils.mkdir_p(File.dirname(catalog_path))
File.write(catalog_path, JSON.pretty_generate({ "format" => "rubygems", "packages" => entries }) + "\n")
