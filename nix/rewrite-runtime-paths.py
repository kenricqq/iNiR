#!/usr/bin/env python3
"""Rewrite Arch-style executable paths in a staged Nix runtime."""

import argparse
from pathlib import Path
import re


SUPPORTED_SUFFIXES = {".qml", ".js", ".sh", ".py"}
EXECUTABLE = re.compile(r"/usr/bin/([A-Za-z0-9][A-Za-z0-9_.+-]*)")


def rewrite_text(source):
    lines = source.splitlines(keepends=True)
    if not lines:
        return source
    start = 1 if lines[0].startswith("#!") else 0
    return "".join(lines[:start] + [EXECUTABLE.sub(r"\1", line) for line in lines[start:]])


def rewrite_tree(root):
    changed = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file() or path.suffix not in SUPPORTED_SUFFIXES:
            continue
        source = path.read_text()
        rewritten = rewrite_text(source)
        if rewritten != source:
            path.write_text(rewritten)
            changed.append(path)
    return changed


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    args = parser.parse_args(argv)
    rewrite_tree(args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
