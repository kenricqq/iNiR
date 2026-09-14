#!/usr/bin/env python3
"""Verify executable resolution in the transformed Nix runtime."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "nix/rewrite-runtime-paths.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("rewrite_runtime_paths", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NixRuntimePathTests(unittest.TestCase):
    def test_rewrite_preserves_shebang_and_uses_path_for_tools(self):
        tool = load_tool()
        source = "#!/usr/bin/env bash\n/usr/bin/notify-send ok\n"
        self.assertEqual(tool.rewrite_text(source), "#!/usr/bin/env bash\nnotify-send ok\n")

    def test_transformed_shell_exec_has_no_relative_executable_checks(self):
        tool = load_tool()
        source = (ROOT / "modules/common/functions/ShellExec.qml").read_text()
        transformed = tool.rewrite_text(source)
        self.assertIn('command -v systemctl', transformed)
        self.assertIn('command -v timeout', transformed)
        self.assertIn('command -v "$systemd_run"', transformed)
        self.assertNotIn('[ -x systemctl ]', transformed)
        self.assertNotIn('[ -x timeout ]', transformed)
        self.assertNotIn('[ -x "$systemd_run" ]', transformed)
        self.assertNotIn('command: ["test", "-x", root.fishPath]', transformed)

    def test_tree_rewrite_only_changes_supported_files(self):
        tool = load_tool()
        with tempfile.TemporaryDirectory(prefix="inir-nix-runtime-") as temp:
            root = Path(temp)
            qml = root / "module.qml"
            qml.write_text('property string command: "/usr/bin/fish"\n')
            binary = root / "asset.bin"
            binary.write_bytes(b"/usr/bin/fish\x00")

            changed = tool.rewrite_tree(root)

            self.assertEqual(changed, [qml])
            self.assertEqual(qml.read_text(), 'property string command: "fish"\n')
            self.assertEqual(binary.read_bytes(), b"/usr/bin/fish\x00")


if __name__ == "__main__":
    unittest.main()
