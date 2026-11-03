# SPDX-License-Identifier: MIT
#
# Whether a wheel installs with a given Python on a given Linux or macOS
# system, read from the compatibility tags in its file name (PEP 425 and
# PEP 600). The build uses this to choose which locked files to fetch for a
# system: the wheels that install, and the sdist of a package with no such
# wheel. uv makes the final choice among the fetched wheels when it
# installs.
{
  # The Python version, such as "3.14".
  pythonVersion,
  # The CPU name in a wheel's platform tag: "x86_64" or "aarch64" on Linux,
  # "arm64" on macOS.
  arch,
  # The glibc version of a Linux system, such as "2.40".
  glibcVersion ? null,
  # The oldest macOS version that a macOS system supports, such as "14.0"
  # (nixpkgs' darwinMinVersion); null on Linux.
  macosVersion ? null,
}:
let
  inherit (builtins)
    any
    elemAt
    filter
    fromJSON
    length
    match
    split
    substring
    stringLength
    ;

  splitOn =
    separator: text:
    filter builtins.isString (split (builtins.replaceStrings [ "." ] [ "\\." ] separator) text);

  versionParts = splitOn "." pythonVersion;
  major = elemAt versionParts 0;
  minor = fromJSON (elemAt versionParts 1);
  cpython = "cp${major}${toString minor}";

  glibcMinor = fromJSON (elemAt (splitOn "." glibcVersion) 1);

  # A python tag that names this major version, or an earlier minor
  # version of it ("py3", "py312").
  pythonTagMatches =
    tag:
    tag == "py${major}"
    || (
      let
        m = match "py${major}([0-9]+)" tag;
      in
      m != null && fromJSON (elemAt m 0) <= minor
    );

  # A stable-ABI tag combination: "cp3X" with "abi3", for X up to this
  # minor version.
  stableAbiMatches =
    tag:
    let
      m = match "cp${major}([0-9]+)" tag;
    in
    m != null && fromJSON (elemAt m 0) <= minor;

  macosRelease = fromJSON (elemAt (splitOn "." macosVersion) 0);

  # The wheels for macOS that install on an arm64 Mac running macosVersion,
  # as packaging.tags.mac_platforms lists them: from macOS 11 on, a release
  # counts the major version, so macosx_<release>_0 for arm64 or universal2
  # from 11 to macosVersion. A universal2 wheel can also name a macOS 10
  # release, for its x86_64 half.
  macosMatches =
    tag:
    let
      m = match "macosx_([0-9]+)_([0-9]+)_(.+)" tag;
      release = fromJSON (elemAt m 0);
      update = fromJSON (elemAt m 1);
      format = elemAt m 2;
    in
    m != null
    && (
      (
        release >= 11
        && release <= macosRelease
        && update == 0
        && (format == arch || format == "universal2")
      )
      || (release == 10 && update >= 4 && update <= 16 && format == "universal2")
    );

  linuxMatches =
    tag:
    let
      legacy = {
        "manylinux1_${arch}" = 5;
        "manylinux2010_${arch}" = 12;
        "manylinux2014_${arch}" = 17;
      };
      # manylinux_2_N: glibc 2.N or later.
      perennial = match "manylinux_2_([0-9]+)_${arch}" tag;
    in
    tag == "linux_${arch}"
    || (legacy ? ${tag} && legacy.${tag} <= glibcMinor)
    || (perennial != null && fromJSON (elemAt perennial 0) <= glibcMinor);

  platformMatches =
    tag: tag == "any" || (if macosVersion != null then macosMatches tag else linuxMatches tag);

  tagsMatch =
    python: abi: platform:
    platformMatches platform
    && (
      (abi == "none" && (pythonTagMatches python || python == cpython))
      || (abi == "abi3" && stableAbiMatches python)
      || (abi == cpython && python == cpython)
    );
in
assert
  glibcVersion != null
  || macosVersion != null
  || throw "purifier: wheels: takes glibcVersion on Linux or macosVersion on macOS";
assert
  macosVersion == null
  || (arch == "arm64" && macosRelease >= 11)
  || throw "purifier: wheels: on macOS, wheels are chosen for arm64 and macOS 11 or later, not ${arch} and ${macosVersion}";
fileName:
let
  stem = substring 0 (stringLength fileName - 4) fileName;
  parts = splitOn "-" stem;
  count = length parts;
  tag = n: splitOn "." (elemAt parts (count - n));
in
count >= 5
&& any (python: any (abi: any (platform: tagsMatch python abi platform) (tag 1)) (tag 2)) (tag 3)
