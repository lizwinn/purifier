# SPDX-License-Identifier: MIT
require "bundler"
require "rubygems/package"
require "tmpdir"
require "fileutils"
require "zlib"
require "open3"
require "json"

Dir.mktmpdir("bundler-nix-input-") do |root|
  repo = File.join(root,"repository")
  FileUtils.mkdir_p(File.join(repo,"gems"))
  specs = []
  names = {"nix-input-a"=>"1.0.0.nix.habcdef1", "nix-input-b"=>"1.0.0.nix.habcdef2"}
  names.each do |name, version|
    directory = File.join(root,name)
    FileUtils.mkdir_p(File.join(directory,"lib"))
    File.write(File.join(directory,"lib",name.tr("-","_")+".rb"),
      name.end_with?("a") ? "require 'nix_input_b'; NixInputA=NixInputB+1\n" : "NixInputB=41\n")
    spec = Gem::Specification.new do |s|
      s.name=name; s.version=version; s.authors=["fixture"];s.summary="native Nix repository proof";s.license="MIT"
      s.files=Dir.chdir(directory){Dir["lib/**/*"]}
      s.add_runtime_dependency("nix-input-b","= "+names["nix-input-b"]) if name.end_with?("a")
    end
    spec.add_runtime_dependency("pathname", ">= 99") if name.end_with?("a")
    filename=Dir.chdir(directory){Gem::Package.build(spec)}
    FileUtils.cp(File.join(directory,filename),File.join(repo,"gems",filename))
    specs << spec
    quick=File.join(repo,"quick","Marshal.4.8");FileUtils.mkdir_p(quick)
    File.binwrite(File.join(quick,spec.original_name+".gemspec.rz"),Zlib::Deflate.deflate(Marshal.dump(spec)))
  end
  triples=specs.map{|s|[s.name,s.version,s.platform.to_s]}
  {"specs"=>triples,"latest_specs"=>triples,"prerelease_specs"=>triples}.each do |kind,values|
    Zlib::GzipWriter.open(File.join(repo,kind+".4.8.gz")){|f|f.write(Marshal.dump(values))}
  end
  projection = File.join(root, "projected-repository")
  input = File.join(root, "projection.json")
  sdk_spec = Gem::Specification.find_by_name("pathname")
  sdk = {"name"=>"pathname", "version"=>sdk_spec.version.to_s, "sdk"=>RbConfig::CONFIG.fetch("prefix"), "ruby"=>RUBY_VERSION, "system"=>Gem::Platform.local.to_s, "dependencies"=>[]}
  File.write(input, JSON.dump({"identity"=>"abcdef123", "archives"=>Dir[File.join(repo,"gems","*.gem")], "intrinsics"=>[sdk]}))
  projector = File.expand_path("../../tools/rubygems_repository.rb", __dir__)
  catalog = File.join(root, "catalog.json")
  raise "projection failed" unless system(Gem.ruby, projector, input, projection, catalog)
  JSON.parse(File.read(catalog)).fetch("packages").each do |entry|
    raise "projected version regressed behind its source" unless Gem::Version.new(entry.fetch("version")) > Gem::Version.new(entry.fetch("sourceVersion"))
  end
  names = JSON.parse(File.read(catalog)).fetch("packages").to_h { |entry| [entry.fetch("name"), entry.fetch("version")] }
  reused = File.join(root, "reused-repository")
  reused_catalog = File.join(root, "reused-catalog.json")
  File.write(input, JSON.dump({"identity"=>"differentproducer", "archives"=>[], "dependencies"=>[{"repository"=>projection,"catalog"=>catalog}]}))
  raise "reuse projection failed" unless system(Gem.ruby, projector, input, reused, reused_catalog)
  raise "provided catalog changed" unless JSON.parse(File.read(catalog)) == JSON.parse(File.read(reused_catalog))
  Dir[File.join(projection,"gems","*.gem")].each do |archive|
    raise "provided archive changed" unless File.binread(archive) == File.binread(File.join(reused,"gems",File.basename(archive)))
  end
  rooted = File.join(root, "rooted-repository")
  rooted_catalog = File.join(root, "rooted-catalog.json")
  File.write(input, JSON.dump({"identity"=>"rooted", "archives"=>[], "roots"=>["nix-input-b"], "dependencies"=>[{"repository"=>projection,"catalog"=>catalog}]}))
  raise "root selection failed" unless system(Gem.ruby, projector, input, rooted, rooted_catalog)
  raise "unrelated publication retained" unless JSON.parse(File.read(rooted_catalog)).fetch("packages").map { |entry| entry.fetch("name") } == ["nix-input-b"]
  %w[specs latest_specs prerelease_specs].each do |kind|
    index = "#{kind}.4.8.gz"
    raise "repository index changed during reuse" unless File.binread(File.join(projection,index)) == File.binread(File.join(reused,index))
    Zlib::GzipReader.open(File.join(projection,index)) do |reader|
      raise "repository index has a wall-clock timestamp" unless reader.mtime.to_i == 0
    end
  end
  repo = reused
  ordinary=File.join(root,"ordinary-registry")
  FileUtils.mkdir_p(ordinary)
  ["specs", "latest_specs", "prerelease_specs"].each do |kind|
    Zlib::GzipWriter.open(File.join(ordinary,kind+".4.8.gz")){|f|f.write(Marshal.dump([]))}
  end
  binding=File.join(root,"binding.rb")
  File.write(binding,<<~RUBY)
    require "rubygems"
    require "json"
    module NixInputRequirements
      def requirement
        versions=JSON.parse(ENV.fetch("NIX_INPUT_VERSIONS","{}"))
        versions.key?(name) ? Gem::Requirement.new("= "+versions[name]) : super
      end
    end
    Gem::Dependency.prepend(NixInputRequirements)
    require "bundler"
    module NixInputDsl
      def gem(name, *args)
        if JSON.parse(ENV.fetch("NIX_INPUT_VERSIONS", "{}")).key?(name)
          options = args.last.is_a?(Hash) ? args.pop.dup : {}
          options[:source] = ENV.fetch("NIX_INPUT_REPOSITORY")
          args << options
        end
        super(name, *args)
      end
    end
    Bundler::Dsl.prepend(NixInputDsl)
  RUBY
  project=File.join(root,"project");FileUtils.mkdir_p(project)
  File.write(File.join(project,"Gemfile"),"source #{("file://"+ordinary).inspect}\ngem 'nix-input-a', '>= 99'\n")
  File.write(File.join(project,"NixGemfile"),"require #{binding.inspect}\neval_gemfile 'Gemfile'\n")
  env={"BUNDLE_GEMFILE"=>File.join(project,"NixGemfile"),"NIX_INPUT_VERSIONS"=>JSON.dump(names),"NIX_INPUT_REPOSITORY"=>"file://"+repo,"BUNDLE_PATH"=>File.join(project,"vendor/bundle"),"BUNDLE_DISABLE_SHARED_GEMS"=>"true"}
  bundle=Gem.bin_path("bundler","bundle")
  raise "native lock failed" unless system(env,Gem.ruby,bundle,"lock","--full-index",chdir:project)
  raise "native install failed" unless system(env,Gem.ruby,bundle,"install","--full-index",chdir:project)
  output,status=Open3.capture2(env,Gem.ruby,bundle,"exec",Gem.ruby,"-e","require 'nix_input_a'; puts NixInputA",chdir:project)
  raise output unless status.success? && output.strip=="42"
  raise "manifest changed" unless File.read(File.join(project,"Gemfile")).include?(">= 99")
  downstream = File.join(root, "ordinary-downstream")
  FileUtils.mkdir_p(downstream)
  File.write(File.join(downstream, "Gemfile"), "source #{("file://"+repo).inspect}\ngem 'nix-input-a', #{("= "+names.fetch("nix-input-a")).inspect}\n")
  ordinary_env = {"BUNDLE_GEMFILE"=>File.join(downstream,"Gemfile"), "BUNDLE_PATH"=>File.join(downstream,"vendor/bundle"), "BUNDLE_DISABLE_SHARED_GEMS"=>"true", "NIX_INPUT_VERSIONS"=>nil, "NIX_INPUT_REPOSITORY"=>nil}
  raise "ordinary downstream install failed" unless system(ordinary_env,Gem.ruby,bundle,"install","--full-index",chdir:downstream)
  output,status=Open3.capture2(ordinary_env,Gem.ruby,bundle,"exec",Gem.ruby,"-e","require 'nix_input_a'; puts NixInputA",chdir:downstream)
  raise "ordinary downstream runtime failed: #{output}" unless status.success? && output.strip=="42"
  puts "RUBYGEMS PROJECTION / NATIVE BINDING / ORDINARY DOWNSTREAM PASSED"
end
