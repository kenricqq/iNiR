#!/usr/bin/env python3
"""Own custom-widget discovery and filesystem mutations.

The command accepts one widget root and exposes three operations: scan, create,
and remove. Widget IDs are a single conservative path segment, and mutations
never follow a widget-directory symlink.
"""

import argparse
import json
from pathlib import Path
import re
import shutil
import sys


ID_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
MAX_ID_LENGTH = 64


class WidgetError(Exception):
    pass


def validate_id(value):
    if not isinstance(value, str) or len(value) > MAX_ID_LENGTH or not ID_PATTERN.fullmatch(value):
        raise WidgetError("widget ID must use lowercase letters, numbers, and single dashes")
    return value


def widget_root(path, create=False):
    root = Path(path).expanduser()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    if not root.exists():
        return root
    if not root.is_dir():
        raise WidgetError("widget root is not a directory")
    return root.resolve()


def child(root, identifier):
    identifier = validate_id(identifier)
    target = root / identifier
    if target.parent != root:
        raise WidgetError("widget path escapes its root")
    return target


def pascal_name(identifier):
    return "".join(part[0].upper() + part[1:] for part in identifier.split("-"))


def scan(path):
    root = widget_root(path)
    if not root.exists():
        return []
    entries = []
    for directory in sorted(root.iterdir(), key=lambda item: item.name):
        try:
            validate_id(directory.name)
        except WidgetError:
            continue
        if directory.is_symlink() or not directory.is_dir():
            continue
        manifest_path = directory / "widget.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        main = manifest.get("main") or pascal_name(directory.name) + ".qml"
        if not isinstance(main, str) or Path(main).name != main or main in {"", ".", ".."}:
            continue
        entries.append({"id": directory.name, "dir": str(directory), "manifest": manifest})
    return entries


def scaffold(identifier):
    name = pascal_name(identifier)
    manifest = {
        "name": name,
        "icon": "widgets",
        "version": "1.0",
        "author": "",
        "description": "Custom desktop widget",
        "category": "custom",
        "main": name + ".qml",
        "defaultConfig": {
            "placementStrategy": "free", "widgetScale": 100,
            "widgetOpacity": 100, "colorMode": "auto", "dim": 0,
            "x": 200, "y": 200,
        },
        "configKeys": {
            "label": {"type": "string", "default": name, "label": "Widget label"},
            "showIcon": {"type": "bool", "default": True, "label": "Show icon"},
        },
        "resizableAxes": {"uniform": "widgetScale"},
        "defaultSize": {"width": 200, "height": 80},
    }
    qml = f'''// {name} — custom iNiR desktop widget
// Full SDK reference: defaults/widgets/WIDGET-SDK.md

import QtQuick
import QtQuick.Layouts
import qs.services
import qs.modules.common
import qs.modules.common.functions
import qs.modules.common.widgets
import qs.modules.background.widgets

AbstractBackgroundWidget {{
    id: root
    configEntryName: "custom.{identifier}"
    defaultConfig: ({{
        placementStrategy: "free", widgetScale: 100, widgetOpacity: 100,
        colorMode: "auto", dim: 0, x: 200, y: 200
    }})
    implicitWidth: content.implicitWidth + Math.round(16 * scaleFactor)
    implicitHeight: content.implicitHeight + Math.round(16 * scaleFactor)
    resizableAxes: ({{ uniform: "widgetScale" }})
    resizeMinWidth: 80
    resizeMinHeight: 40

    Rectangle {{
        anchors.fill: parent
        radius: root.cornerRadiusOverride >= 0 ? root.cornerRadiusOverride : Appearance.rounding.normal
        color: root.backgroundOpacity > 0 ? ColorUtils.applyAlpha(root.colText, root.backgroundOpacity) : "transparent"
        border {{ width: root.borderWidth; color: ColorUtils.applyAlpha(root.colText, root.borderOpacity) }}
    }}

    Column {{
        id: content
        anchors.centerIn: parent
        spacing: Math.round(6 * root.scaleFactor)
        MaterialSymbol {{
            anchors.horizontalCenter: parent.horizontalCenter
            text: "schedule"
            iconSize: Math.round(20 * root.scaleFactor)
            color: root.colText
        }}
        StyledText {{
            anchors.horizontalCenter: parent.horizontalCenter
            text: root._readConfigKey("label") ?? "{name}"
            font.pixelSize: Math.round(Appearance.font.pixelSize.small * root.scaleFactor)
            color: root.colText
        }}
    }}
}}
'''
    return manifest, qml


def create(path, identifier):
    validate_id(identifier)
    root = widget_root(path, create=True)
    target = child(root, identifier)
    try:
        target.mkdir(mode=0o700)
    except FileExistsError as error:
        raise WidgetError("widget already exists") from error
    manifest, qml = scaffold(identifier)
    try:
        (target / "widget.json").write_text(json.dumps(manifest, indent=4) + "\n")
        (target / manifest["main"]).write_text(qml)
    except Exception:
        shutil.rmtree(target)
        raise
    return {"status": "created", "id": identifier}


def remove(path, identifier):
    root = widget_root(path)
    if not root.exists():
        raise WidgetError("widget does not exist")
    target = child(root, identifier)
    if target.is_symlink():
        raise WidgetError("refusing to remove a widget-directory symlink")
    if not target.exists() or not target.is_dir():
        raise WidgetError("widget does not exist")
    shutil.rmtree(target)
    return {"status": "removed", "id": identifier}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("scan", "create", "remove"))
    parser.add_argument("identifier", nargs="?")
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "scan":
            if args.identifier is not None:
                raise WidgetError("scan does not accept a widget ID")
            result = scan(args.root)
        else:
            if args.identifier is None:
                raise WidgetError(f"{args.action} requires a widget ID")
            result = create(args.root, args.identifier) if args.action == "create" else remove(args.root, args.identifier)
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (OSError, WidgetError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
