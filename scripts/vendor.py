#!/usr/bin/env python3
"""
vendor.py: copy buzz-engine INTO a repo, for runners that can't load plugins.

For an interactive host without plugin loading, export commands and their supporting
files into the project. This installs files only, not a scheduler or credential boundary.
Unattended runs use the isolated GitHub Actions workflow. Every plugin path is rewritten:

  .claude/commands/buzz.md, buzz-daily.md, buzz-init.md
  .claude/agents/buzz-humanizer.md
  buzz/engine/{scripts,rubrics,templates,docs}/...   + buzz/engine/VERSION

Commit the result. Re-run it to update; it overwrites only files it wrote
before, tracked by content hashes in buzz/engine/.vendor-manifest.json. Edited
or unowned files need --force; symlink destinations are always refused.

Usage:
  python3 <buzz-engine>/scripts/vendor.py --into /path/to/repo
  python3 <buzz-engine>/scripts/vendor.py --into . --dry-run
"""
import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_REL = "buzz/engine"
MARKER = "vendored from buzz-engine"
MANIFEST_REL = ENGINE_REL + "/.vendor-manifest.json"
TEXT_EXT = (".md", ".py", ".yaml", ".yml", ".txt")


def version():
    with open(os.path.join(SRC, ".claude-plugin", "plugin.json")) as f:
        return json.load(f)["version"]


def rewrite(text):
    text = text.replace("${CLAUDE_PLUGIN_ROOT}", ENGINE_REL)
    text = text.replace("buzz-engine:humanizer", "buzz-humanizer")
    text = text.replace("/buzz-engine:", "/")
    return text


def mark(text, ver, is_md):
    note = ("%s v%s. Don't edit: update by re-running vendor.py from a "
            "buzz-engine checkout." % (MARKER, ver))
    if not is_md:
        return text
    line = "<!-- %s -->\n" % note
    if text.startswith("---\n"):
        end = text.index("\n---\n", 4) + 5
        return text[:end] + line + text[end:]
    return line + text


def plan(dest):
    """(source path, destination path, is_command_or_agent) for every file."""
    out = []
    for name in ("buzz.md", "buzz-daily.md", "buzz-init.md"):
        out.append((os.path.join(SRC, "commands", name),
                    os.path.join(dest, ".claude", "commands", name), True))
    out.append((os.path.join(SRC, "agents", "humanizer.md"),
                os.path.join(dest, ".claude", "agents", "buzz-humanizer.md"), True))
    for sub in ("scripts", "rubrics", "templates", "docs"):
        root = os.path.join(SRC, sub)
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d != "__pycache__"]
            for fn in fns:
                if fn.endswith(".pyc") or fn in ("vendor.py", "install-codex.py"):
                    continue
                s = os.path.join(dp, fn)
                out.append((s, os.path.join(dest, ENGINE_REL, os.path.relpath(s, SRC)), False))
    for name in ("LICENSE", "NOTICE"):
        out.append((os.path.join(SRC, name), os.path.join(dest, ENGINE_REL, name), False))
    return out


def check_path(dest, path):
    """Reject links before reading or writing any part of the output plan."""
    if os.path.commonpath((dest, path)) != dest:
        raise ValueError("destination escapes project: %s" % path)
    current = dest
    parts = [""] + os.path.relpath(path, dest).split(os.path.sep)
    for index, part in enumerate(parts):
        current = os.path.normpath(os.path.join(current, part))
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError("refusing symlink destination: %s" % current)
        if index < len(parts) - 1 and not stat.S_ISDIR(mode):
            raise ValueError("destination parent is not a directory: %s" % current)
        if index == len(parts) - 1 and not stat.S_ISREG(mode):
            raise ValueError("destination is not a regular file: %s" % current)


def payload(dest, ver):
    outputs = {}
    for source, target, top in plan(dest):
        rel = os.path.relpath(target, dest)
        with open(source, "rb") as handle:
            content = handle.read()
        if source.endswith(TEXT_EXT):
            text = rewrite(content.decode("utf-8"))
            if top and rel.endswith("buzz-humanizer.md"):
                text = text.replace("\nname: humanizer\n", "\nname: buzz-humanizer\n", 1)
            content = mark(text, ver, top).encode("utf-8")
        outputs[rel] = content
    outputs[ENGINE_REL + "/VERSION"] = (ver + "\n").encode("utf-8")
    hashes = {rel: hashlib.sha256(content).hexdigest() for rel, content in outputs.items()}
    outputs[MANIFEST_REL] = (json.dumps({"format": 1, "files": hashes},
                                      indent=2, sort_keys=True) + "\n").encode("utf-8")
    return outputs


def preflight(dest, outputs, force):
    for rel in outputs:
        check_path(dest, os.path.join(dest, rel))
    manifest = {}
    manifest_path = os.path.join(dest, MANIFEST_REL)
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as handle:
                stored = json.load(handle)
            if stored.get("format") != 1 or not isinstance(stored.get("files"), dict):
                raise ValueError("invalid manifest format")
            manifest = stored["files"]
        except (ValueError, AttributeError, UnicodeError):
            if not force:
                raise ValueError("unrecognized ownership manifest; use --force to replace: %s" % MANIFEST_REL)
    blocked = []
    for rel, content in outputs.items():
        path = os.path.join(dest, rel)
        if not os.path.exists(path) or force:
            continue
        with open(path, "rb") as handle:
            previous = handle.read()
        if previous == content:
            continue
        if rel == MANIFEST_REL and manifest:
            continue
        if manifest.get(rel) != hashlib.sha256(previous).hexdigest():
            blocked.append(rel)
    if blocked:
        raise ValueError("these files exist and are unowned or edited:\n  %s\n"
                         "Move them aside, or re-run with --force to replace them."
                         % "\n  ".join(blocked))


def write_output(dest, path, content):
    # Replace the directory entry rather than truncating a possibly hardlinked file.
    if os.path.exists(path):
        mode = stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)
    else:
        mask = os.umask(0)
        os.umask(mask)
        mode = 0o666 & ~mask
    descriptor, temporary = tempfile.mkstemp(prefix=".buzz-vendor-", dir=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.chmod(temporary, mode)
        check_path(dest, path)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--into", required=True, help="repo root to vendor into")
    ap.add_argument("--force", action="store_true",
                    help="overwrite files that weren't written by this script")
    ap.add_argument("--dry-run", action="store_true", help="list what would be written")
    args = ap.parse_args()

    dest = os.path.abspath(args.into)
    if not os.path.isdir(dest):
        sys.exit("no such directory: %s" % dest)
    if os.path.samefile(dest, SRC):
        sys.exit("refusing to vendor buzz-engine into itself")
    ver = version()

    outputs = payload(dest, ver)
    try:
        preflight(dest, outputs, args.force)
    except ValueError as error:
        sys.exit(str(error))
    for rel, content in outputs.items():
        if args.dry_run:
            print("would write", rel)
            continue
        path = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        check_path(dest, path)
        # Unchanged files need no write, including legacy installs without a manifest.
        if os.path.exists(path):
            with open(path, "rb") as handle:
                if handle.read() == content:
                    continue
        write_output(dest, path, content)
    print("%s buzz-engine v%s into %s (%d files)"
          % ("Would vendor" if args.dry_run else "Vendored", ver, dest, len(outputs)))
    if not args.dry_run:
        print("Commit .claude/commands/buzz*.md, .claude/agents/buzz-humanizer.md and buzz/engine/.")
        print("Don't also enable the plugin in this repo: /buzz would exist twice.")


if __name__ == "__main__":
    main()
