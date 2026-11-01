# SPDX-License-Identifier: MIT
#
# Writes a shallow .git directory for a plain git checkout, from the raw
# commit object that a lockfile recorded:
#
#   python3 dot-git.py <checkout> <commit object file> <rev> <entry path>
#
# Every file, symbolic link and directory of the checkout becomes a loose
# git object. The commit object is written as recorded, after two checks:
# its hash must be <rev>, and its tree must be the tree written from the
# checkout. HEAD names the branch `main`, which points at the commit, and
# .git/shallow lists the commit when it has parents. The result depends
# only on the checkout and the commit object: objects are compressed at a
# fixed level, and nothing records a time or a path outside the checkout.
import hashlib
import os
import stat
import sys
import zlib

checkout, commit_file, rev, entry_path = sys.argv[1:5]
git_dir = os.path.join(checkout, ".git")


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


# The tree object of a directory, or None for a directory with no files
# under it, which git does not record.
def write_tree(directory):
    entries = []
    for name in os.listdir(directory):
        if directory == checkout and name == ".git":
            continue
        path = os.path.join(directory, name)
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode):
            mode, digest = b"120000", write_object("blob", os.fsencode(os.readlink(path)))
        elif stat.S_ISDIR(info.st_mode):
            digest = write_tree(path)
            if digest is None:
                continue
            mode = b"40000"
        elif stat.S_ISREG(info.st_mode):
            with open(path, "rb") as f:
                data = f.read()
            mode = b"100755" if info.st_mode & stat.S_IXUSR else b"100644"
            digest = write_object("blob", data)
        else:
            fail(f"{path} is neither a file, a symbolic link nor a directory")
        # git orders the entries of a tree by name, with a "/" after the
        # name of a directory.
        key = os.fsencode(name) + (b"/" if mode == b"40000" else b"")
        entries.append((key, mode, os.fsencode(name), digest))
    if not entries:
        return None
    entries.sort()
    data = b"".join(
        mode + b" " + name + b"\0" + bytes.fromhex(digest) for _, mode, name, digest in entries
    )
    return write_object("tree", data)


with open(commit_file, "rb") as f:
    commit = f.read()

os.makedirs(os.path.join(git_dir, "refs", "heads"))
os.makedirs(os.path.join(git_dir, "refs", "tags"))

tree = write_tree(checkout)
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
    fail(f"its commitObject names the tree {recorded_tree}, and the checkout is the tree {tree}")

with open(os.path.join(git_dir, "HEAD"), "w") as f:
    f.write("ref: refs/heads/main\n")
with open(os.path.join(git_dir, "refs", "heads", "main"), "w") as f:
    f.write(rev + "\n")
with open(os.path.join(git_dir, "config"), "w") as f:
    f.write("[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n")
if any(line.startswith(b"parent ") for line in headers):
    with open(os.path.join(git_dir, "shallow"), "w") as f:
        f.write(rev + "\n")
