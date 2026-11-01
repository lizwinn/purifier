# SPDX-License-Identifier: MIT
#
# The shapes of a download entry, and the check that an entry has one of
# them. The kind of an entry is decided by which kind key it has:
#
#   file     { path, url, mirrors, hash }
#   inline   { path, contents }
#   git      { path, git = { url, rev, submodules, leaveDotGit, commitObject }, hash }
#   archive  { path, archive = { url, mirrors, stripTop }, hash }
#   hg       { path, hg = { url, rev, subrepos }, hash }
#   svn      { path, svn = { url, rev, ignoreExternals, ignoreKeywords }, hash }
#   fossil   { path, fossil = { url, rev }, hash }
#   bzr      { path, bzr = { url, rev }, hash }
#   darcs    { path, darcs = { url, rev, context }, hash }
#   pijul    { path, pijul = { url, rev }, hash }
#   cvs      { path, cvs = { cvsRoot, module, tag, date }, hash }
#
# Every field is required. Of `cvs.tag` and `cvs.date`, and of
# `darcs.rev` and `darcs.context`, exactly one is a string and the other
# is null. A git entry has a `git.commitObject` exactly when
# `git.leaveDotGit` is true, and null otherwise. It is
# `{ object, gitlinks }`: `object` is the raw commit object of `git.rev`
# in base64, and `gitlinks` lists the submodules of the commit's tree as
# `{ path, rev }`. An entry may carry other top-level fields, such
# as the `os`, `cpu` and `libc` filters of npm, and the check leaves them
# alone.
{ lib }:
let
  inherit (builtins)
    all
    attrNames
    concatStringsSep
    elem
    filter
    head
    isAttrs
    isBool
    isInt
    isList
    isString
    length
    split
    toJSON
    ;

  string = {
    test = isString;
    description = "a string";
  };
  bool = {
    test = isBool;
    description = "a boolean";
  };
  strings = {
    test = v: isList v && all isString v;
    description = "a list of strings";
  };
  attrs = {
    test = isAttrs;
    description = "an attribute set";
  };
  nullOrAttrs = {
    test = v: v == null || isAttrs v;
    description = "null or an attribute set";
  };
  nullOrString = {
    test = v: v == null || isString v;
    description = "null or a string";
  };
  # Subversion revisions are numbers, which a lockfile written as JSON
  # holds as integers.
  stringOrInt = {
    test = v: isString v || isInt v;
    description = "a string or an integer";
  };

  # For each kind: the key that marks it, its top-level fields besides
  # `path`, and the fields of the attribute set under the key (null for a
  # kind whose key is a plain field).
  kinds = {
    file = {
      key = "url";
      fields = {
        url = string;
        mirrors = strings;
        hash = string;
      };
      sub = null;
    };
    inline = {
      key = "contents";
      fields = {
        contents = string;
      };
      sub = null;
    };
    git = {
      key = "git";
      fields = {
        git = attrs;
        hash = string;
      };
      sub = {
        url = string;
        rev = string;
        submodules = bool;
        leaveDotGit = bool;
        commitObject = nullOrAttrs;
      };
    };
    archive = {
      key = "archive";
      fields = {
        archive = attrs;
        hash = string;
      };
      sub = {
        url = string;
        mirrors = strings;
        stripTop = bool;
      };
    };
    hg = {
      key = "hg";
      fields = {
        hg = attrs;
        hash = string;
      };
      sub = {
        url = string;
        rev = string;
        subrepos = bool;
      };
    };
    svn = {
      key = "svn";
      fields = {
        svn = attrs;
        hash = string;
      };
      sub = {
        url = string;
        rev = stringOrInt;
        ignoreExternals = bool;
        ignoreKeywords = bool;
      };
    };
    fossil = {
      key = "fossil";
      fields = {
        fossil = attrs;
        hash = string;
      };
      sub = {
        url = string;
        rev = string;
      };
    };
    bzr = {
      key = "bzr";
      fields = {
        bzr = attrs;
        hash = string;
      };
      sub = {
        url = string;
        rev = string;
      };
    };
    darcs = {
      key = "darcs";
      fields = {
        darcs = attrs;
        hash = string;
      };
      sub = {
        url = string;
        rev = nullOrString;
        context = nullOrString;
      };
      oneOf = [
        "rev"
        "context"
      ];
    };
    pijul = {
      key = "pijul";
      fields = {
        pijul = attrs;
        hash = string;
      };
      sub = {
        url = string;
        rev = string;
      };
    };
    cvs = {
      key = "cvs";
      fields = {
        cvs = attrs;
        hash = string;
      };
      sub = {
        cvsRoot = string;
        module = string;
        tag = nullOrString;
        date = nullOrString;
      };
      oneOf = [
        "tag"
        "date"
      ];
    };
  };

  kindNames = attrNames kinds;
  kindKeys = map (kind: kinds.${kind}.key) kindNames;

  quoted = names: concatStringsSep ", " (map (n: "`${n}`") names);

  # The problem with a path, or null. A path is relative, and each of its
  # components is a name: not empty, not "." and not "..".
  pathProblem =
    path:
    let
      components = filter isString (split "/" path);
    in
    if !isString path then
      "its `path` is not a string"
    else if path == "" then
      "its `path` is empty"
    else if lib.hasPrefix "/" path then
      "its `path` is absolute; a path is relative to the root of the tree"
    else if elem ".." components then
      "its `path` has a `..` component"
    else if elem "." components then
      "its `path` has a `.` component"
    else if elem "" components then
      "its `path` has an empty component (a doubled or trailing `/`)"
    else
      null;

  # The problems with the fields of one attribute set, against a field
  # table. `prefix` names the set in a message.
  fieldProblems =
    prefix: table: set:
    let
      missing = filter (field: !(set ? ${field})) (attrNames table);
      mistyped = filter (field: set ? ${field} && !table.${field}.test set.${field}) (attrNames table);
    in
    lib.optional (missing != [ ]) "it has no ${quoted (map (f: prefix + f) missing)}"
    ++ map (field: "its `${prefix}${field}` is not ${table.${field}.description}") mistyped;

  # A gitlink names a submodule commit by its full SHA-1.
  gitlinkTable = {
    path = string;
    rev = {
      test = v: isString v && builtins.match "[0-9a-f]{40}" v != null;
      description = "a SHA-1 of 40 lowercase hexadecimal digits";
    };
  };

  # The problem with a `git.commitObject` set, or null.
  commitObjectProblem =
    commitObject:
    let
      own = fieldProblems "git.commitObject." {
        object = string;
        gitlinks = {
          test = v: isList v && all isAttrs v;
          description = "a list of attribute sets";
        };
      } commitObject;
      links = lib.imap0 (
        n: link:
        let
          prefix = "git.commitObject.gitlinks.${toString n}.";
          fields = fieldProblems prefix gitlinkTable link;
          pathIssue = pathProblem link.path;
        in
        if fields != [ ] then
          concatStringsSep "; " fields
        else if pathIssue != null then
          builtins.replaceStrings [ "`path`" ] [ "`${prefix}path`" ] pathIssue
        else
          null
      ) commitObject.gitlinks;
      linkIssues = filter (p: p != null) links;
      paths = map (link: link.path) commitObject.gitlinks;
    in
    if own != [ ] then
      concatStringsSep "; " own
    else if linkIssues != [ ] then
      concatStringsSep "; " linkIssues
    else if lib.length (lib.unique paths) != length paths then
      "its `git.commitObject.gitlinks` list a path more than once"
    else
      null;

  kindProblems =
    kind: entry:
    let
      spec = kinds.${kind};
      top = fieldProblems "" spec.fields entry;
      sub =
        if spec.sub == null || top != [ ] then
          [ ]
        else
          fieldProblems "${spec.key}." spec.sub entry.${spec.key};
      # A kind with `oneOf` takes exactly one of two fields under its key,
      # and the other is null.
      oneOf =
        let
          set = entry.${spec.key};
          a = builtins.elemAt spec.oneOf 0;
          b = builtins.elemAt spec.oneOf 1;
        in
        lib.optional (spec ? oneOf && top == [ ] && sub == [ ] && (set.${a} == null) == (set.${b} == null))
          "exactly one of its `${spec.key}.${a}` and `${spec.key}.${b}` must be a string, and the other null";
      # The .git directory is rebuilt from the checkout, the commit object
      # and the gitlinks.
      git = lib.optional (kind == "git" && top == [ ] && sub == [ ]) (
        if entry.git.leaveDotGit && entry.git.commitObject == null then
          "its `git.leaveDotGit` is true, and its `git.commitObject` is null"
        else if !entry.git.leaveDotGit && entry.git.commitObject != null then
          "its `git.leaveDotGit` is false, and its `git.commitObject` is not null"
        else if entry.git.commitObject == null then
          null
        else
          commitObjectProblem entry.git.commitObject
      );
    in
    top ++ sub ++ oneOf ++ filter (p: p != null) git;

  # The kind keys an entry has.
  keysOf = entry: filter (key: entry ? ${key}) kindKeys;

  # The kind of a checked entry.
  kindOf = entry: head (filter (kind: entry ? ${kinds.${kind}.key}) kindNames);

  # The problem with an entry, as a message that names the entry's path, or
  # null.
  problem =
    entry:
    let
      named = message: "purifier: the entry at path ${toJSON entry.path}: ${message}";
      keys = keysOf entry;
      pathIssue = pathProblem entry.path;
      issues = kindProblems (kindOf entry) entry;
    in
    if !isAttrs entry then
      "purifier: an entry is not an attribute set: ${toJSON entry}"
    else if !(entry ? path) then
      "purifier: an entry has no `path`; its keys are ${quoted (attrNames entry)}"
    else if !isString entry.path then
      "purifier: an entry's `path` is not a string: ${toJSON entry.path}"
    else if pathIssue != null then
      named pathIssue
    else if keys == [ ] then
      named "it has no kind key; an entry has exactly one of ${quoted kindKeys}"
    else if length keys > 1 then
      named "it has the kind keys ${quoted keys}; an entry has exactly one"
    else if issues != [ ] then
      named (concatStringsSep "; " issues)
    else
      null;

  # The entry, or an evaluation error with its problem.
  check =
    entry:
    let
      p = problem entry;
    in
    if p == null then entry else throw p;

  # A store path name from a file name. Store path names allow
  # [A-Za-z0-9+._?=-], must not start with a dot, and are at most 211
  # characters; every other character becomes "_".
  storeName =
    fileName:
    let
      replaced = concatStringsSep "" (
        map (part: if isList part then "_" else part) (split "[^A-Za-z0-9+._?=-]" fileName)
      );
      undotted = head (builtins.match "\\.*(.*)" replaced);
      nonEmpty = if undotted == "" then "download" else undotted;
    in
    builtins.substring 0 200 nonEmpty;
in
{
  inherit
    kinds
    kindOf
    problem
    check
    storeName
    ;
}
