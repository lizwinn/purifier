# SPDX-License-Identifier: MIT
#
# Writes a shallow .git directory for a plain git checkout, from the raw
# commit object and the submodule commits that a lockfile recorded:
#
#   python3 dot-git.py <checkout> <commit object file> <gitlinks file> <rev> <entry path>
#
# The gitlinks file is a JSON list of { path, rev }: the path of each
# submodule in the commit's tree, and the commit it records there.
#
# Every file and symbolic link of the checkout becomes a loose git object,
# and so does every directory but those at gitlink paths. A gitlink path
# becomes a gitlink entry (mode 160000) with its recorded commit, whatever
# the checkout holds there. The commit object is written as recorded,
# after two checks: its hash must be <rev>, and its tree must be the tree
# written from the checkout. HEAD names the branch `main`, which points at
# the commit, .git/shallow lists the commit when it has parents, and the
# index lists every entry of the tree, so `git status` finds no change.
#
# The result depends only on the checkout, the commit object and the
# gitlinks. Objects are compressed at a fixed level, and the index has
# zeros for every stat field, which makes git compare the content of each
# file rather than trust the stat data.
import hashlib
import json
import os
import stat
import struct
import sys
import zlib

checkout, commit_file, gitlinks_file, rev, entry_path = sys.argv[1:6]
git_dir = os.path.join(checkout, ".git")

TREE = 0o40000
GITLINK = 0o160000


def fail(message):
    print(f'purifier: the git entry at path "{entry_path}": {message}', file=sys.stderr)
    sys.exit(1)


def write_object(kind, data):
    body = kind.encode() + b" " + str(len(data)).encode() + b"\0" + data
    digest = hashlib.sha1(body).hexdigest()
    directory = os.path.join(git_dir, "objects", digest[:2])
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, digest[2:])
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(zlib.compress(body, 6))
    return digest


with open(gitlinks_file) as f:
    gitlinks = {link["path"]: link["rev"] for link in json.load(f)}

# The names that the gitlinks put in each directory, by the directory's
# path ("" for the root): a gitlink, or a directory on the way to one.
implied = {}
for link in gitlinks:
    parts = link.split("/")
    for depth in range(len(parts)):
        implied.setdefault("/".join(parts[:depth]), set()).add(parts[depth])

# The entries of the index: (path, mode, object id).
index_entries = []


# The tree object of a directory, or None for a directory with nothing
# under it, which git does not record. `rel` is the directory's path in
# the repository.
def write_tree(rel):
    directory = os.path.join(checkout, rel)
    names = set(os.listdir(directory)) if os.path.isdir(directory) else set()
    names |= implied.get(rel, set())
    if rel == "":
        names.discard(".git")
    entries = []
    for name in names:
        path_rel = f"{rel}/{name}" if rel else name
        path = os.path.join(checkout, path_rel)
        if path_rel in gitlinks:
            mode, digest = GITLINK, gitlinks[path_rel]
        elif path_rel in implied or (os.path.isdir(path) and not os.path.islink(path)):
            digest = write_tree(path_rel)
            if digest is None:
                continue
            mode = TREE
        elif os.path.islink(path):
            mode, digest = 0o120000, write_object("blob", os.fsencode(os.readlink(path)))
        elif os.path.isfile(path):
            info = os.lstat(path)
            with open(path, "rb") as f:
                data = f.read()
            mode = 0o100755 if info.st_mode & stat.S_IXUSR else 0o100644
            digest = write_object("blob", data)
        else:
            fail(f"{path_rel} is neither a file, a symbolic link nor a directory")
        if mode != TREE:
            index_entries.append((os.fsencode(path_rel), mode, digest))
        # git orders the entries of a tree by name, with a "/" after the
        # name of a directory.
        key = os.fsencode(name) + (b"/" if mode == TREE else b"")
        entries.append((key, mode, os.fsencode(name), digest))
    if not entries:
        return None
    entries.sort()
    data = b"".join(
        b"%o %s\0" % (mode, name) + bytes.fromhex(digest) for _, mode, name, digest in entries
    )
    return write_object("tree", data)


# An index of version 2, whose entries have zero stat data.
def write_index():
    body = b"DIRC" + struct.pack(">II", 2, len(index_entries))
    for path, mode, digest in sorted(index_entries):
        entry = struct.pack(">10I", 0, 0, 0, 0, 0, 0, mode, 0, 0, 0)
        entry += bytes.fromhex(digest)
        entry += struct.pack(">H", min(len(path), 0xFFF))
        entry += path
        # NUL padding to a multiple of eight bytes, with at least one NUL.
        entry += b"\0" * (8 - len(entry) % 8)
        body += entry
    with open(os.path.join(git_dir, "index"), "wb") as f:
        f.write(body + hashlib.sha1(body).digest())


with open(commit_file, "rb") as f:
    commit = f.read()

os.makedirs(os.path.join(git_dir, "objects"), exist_ok=True)
os.makedirs(os.path.join(git_dir, "refs", "heads"))
os.makedirs(os.path.join(git_dir, "refs", "tags"))

tree = write_tree("")
if tree is None:
    tree = write_object("tree", b"")

headers = commit.split(b"\n\n", 1)[0].split(b"\n")
if not headers[0].startswith(b"tree "):
    fail("its commitObject does not start with a tree line")
recorded_tree = headers[0][5:].decode()

digest = write_object("commit", commit)
if digest != rev:
    fail(f"its commitObject hashes to {digest}, and not to its rev {rev}")
if recorded_tree != tree:
    fail(f"its commitObject names the tree {recorded_tree}, and the checkout with its gitlinks is the tree {tree}")

write_index()
with open(os.path.join(git_dir, "HEAD"), "w") as f:
    f.write("ref: refs/heads/main\n")
with open(os.path.join(git_dir, "refs", "heads", "main"), "w") as f:
    f.write(rev + "\n")
with open(os.path.join(git_dir, "config"), "w") as f:
    f.write("[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n")
if any(line.startswith(b"parent ") for line in headers):
    with open(os.path.join(git_dir, "shallow"), "w") as f:
        f.write(rev + "\n")
