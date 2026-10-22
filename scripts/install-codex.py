#!/usr/bin/env python3
"""Link the upstream Buzz skill into a project's .agents/skills; no files are copied."""
import argparse
import os
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent


def install(into, dry_run=False):
    target = Path(into).resolve()
    if not target.is_dir():
        raise ValueError("target must be an existing project directory")
    for parent in (target / ".agents", target / ".agents" / "skills"):
        if parent.is_symlink():
            raise ValueError("skill parent must not be a symlink: %s" % parent)
        if parent.exists() and not parent.is_dir():
            raise ValueError("skill parent must be a directory: %s" % parent)
    link = target / ".agents" / "skills" / "buzz-engine"
    if os.path.lexists(link):
        if link.is_symlink() and link.resolve() == ENGINE:
            return "already linked: %s" % link
        raise ValueError("existing skill would be replaced: %s" % link)
    if not dry_run:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(os.path.relpath(ENGINE, link.parent), target_is_directory=True)
    return "%s %s -> %s" % ("would link" if dry_run else "linked", link, ENGINE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--into", required=True, help="project repository root")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        print(install(args.into, args.dry_run))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
