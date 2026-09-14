#!/usr/bin/env python3
"""Behavioral tests for the custom-widget filesystem command."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest


TOOL = Path(__file__).with_name("custom-widget-files.py")


class CustomWidgetFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="inir-custom-widgets-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.widgets = self.base / "widgets"

    def run_tool(self, *args, success=True):
        result = subprocess.run(
            ["python3", str(TOOL), *args, "--root", str(self.widgets)],
            text=True,
            capture_output=True,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def test_create_scan_and_remove_round_trip(self):
        created = json.loads(self.run_tool("create", "my-widget").stdout)
        self.assertEqual(created, {"id": "my-widget", "status": "created"})
        manifest = json.loads((self.widgets / "my-widget/widget.json").read_text())
        self.assertEqual(manifest["main"], "MyWidget.qml")
        self.assertTrue((self.widgets / "my-widget/MyWidget.qml").is_file())

        scanned = json.loads(self.run_tool("scan").stdout)
        self.assertEqual([entry["id"] for entry in scanned], ["my-widget"])
        self.assertEqual(scanned[0]["manifest"]["name"], "MyWidget")

        removed = json.loads(self.run_tool("remove", "my-widget").stdout)
        self.assertEqual(removed, {"id": "my-widget", "status": "removed"})
        self.assertFalse((self.widgets / "my-widget").exists())

    def test_invalid_identifiers_never_escape_root(self):
        outside = self.base / "unrelated"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_text("keep")

        for identifier in ["", ".", "..", "../unrelated", "/tmp/x", "bad/name",
                           "bad name", "$(touch nope)", "Uppercase", "trailing-"]:
            with self.subTest(identifier=identifier):
                self.run_tool("remove", identifier, success=False)
                self.run_tool("create", identifier, success=False)

        self.assertEqual(sentinel.read_text(), "keep")
        self.assertEqual(list(self.widgets.glob("*")) if self.widgets.exists() else [], [])

    def test_remove_rejects_symlinks_and_preserves_target(self):
        outside = self.base / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_text("keep")
        self.widgets.mkdir()
        (self.widgets / "linked-widget").symlink_to(outside, target_is_directory=True)

        self.run_tool("remove", "linked-widget", success=False)

        self.assertEqual(sentinel.read_text(), "keep")
        self.assertTrue((self.widgets / "linked-widget").is_symlink())

    def test_create_does_not_overwrite_existing_widget(self):
        self.run_tool("create", "clock")
        manifest = self.widgets / "clock/widget.json"
        manifest.write_text("user data")

        self.run_tool("create", "clock", success=False)

        self.assertEqual(manifest.read_text(), "user data")

    def test_scan_uses_json_encoding_and_rejects_escaping_main_paths(self):
        self.widgets.mkdir()
        good = self.widgets / "safe-widget"
        good.mkdir()
        (good / "widget.json").write_text(json.dumps({"name": 'Quote " Widget', "main": "Safe.qml"}))
        (good / "Safe.qml").write_text("Item {}")
        unsafe = self.widgets / "unsafe-widget"
        unsafe.mkdir()
        (unsafe / "widget.json").write_text(json.dumps({"name": "Unsafe", "main": "../Outside.qml"}))

        scanned = json.loads(self.run_tool("scan").stdout)

        self.assertEqual([entry["id"] for entry in scanned], ["safe-widget"])
        self.assertEqual(scanned[0]["manifest"]["name"], 'Quote " Widget')


if __name__ == "__main__":
    unittest.main()
