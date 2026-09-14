"""Run the real stdin/stdout entrypoint with discovery disabled and loopback only."""

import json
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

BACKEND = Path(__file__).resolve().parents[1]

# Substitute only network exposure and the downloads destination. The actual
# entrypoint, TLS identity, control loop, threads, signals and cleanup are tested.
CHILD = '''
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import bridge
base = Path(sys.argv[2])
bind_port = int(sys.argv[3])
real_server = bridge.Server
def loopback_server(address, context, owner):
    server = real_server(("127.0.0.1", bind_port), context, owner)
    (base / "port").write_text(str(server.server_address[1]))
    return server
class Discovery:
    def __init__(self, owner):
        self.owner = owner
        self.socket = SimpleNamespace(close=lambda: None)
    def run(self): self.owner.stopping.wait()
    def announce(self): pass
bridge.Server = loopback_server
bridge.Discovery = Discovery
bridge.downloads_directory = lambda: base / "received"
sys.argv = ["bridge", "--state-dir", str(base / "identity")]
raise SystemExit(bridge.main())
'''


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.children = []
        self.addCleanup(self.close_children)

    def close_children(self):
        for process in self.children:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def start(self, port=0, only_ready=False):
        process = subprocess.Popen([sys.executable, "-u", "-c", CHILD, str(BACKEND), str(self.base), str(port)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(process)
        events = queue.Queue()

        def read():
            for line in process.stdout:
                events.put(json.loads(line))
                if only_ready:
                    return

        threading.Thread(target=read, daemon=True).start()
        event = events.get(timeout=10)
        return process, event, events

    def assert_listener_closed(self, port):
        with socket.socket() as connection:
            connection.settimeout(1)
            self.assertNotEqual(connection.connect_ex(("127.0.0.1", port)), 0)

    def test_eof_stops_the_endpoint(self):
        process, event, _ = self.start()
        self.assertEqual(event["event"], "ready")
        port = int((self.base / "port").read_text())
        process.stdin.close()
        self.assertEqual(process.wait(timeout=5), 0)
        self.assert_listener_closed(port)
        self.assertEqual(process.stderr.read(), "")

    def test_stop_command_closes_listener_and_allows_restart(self):
        process, event, _ = self.start()
        self.assertEqual(event["event"], "ready")
        port = int((self.base / "port").read_text())
        process.stdin.write('{"action":"stop"}\n')
        process.stdin.flush()
        self.assertEqual(process.wait(timeout=5), 0)
        self.assert_listener_closed(port)
        second, event, _ = self.start(port)
        self.assertEqual(event["event"], "ready")
        second.stdin.close()
        self.assertEqual(second.wait(timeout=5), 0)

    def test_sigterm_closes_listener(self):
        process, event, _ = self.start()
        self.assertEqual(event["event"], "ready")
        port = int((self.base / "port").read_text())
        process.terminate()
        self.assertEqual(process.wait(timeout=5), 0)
        self.assert_listener_closed(port)

    def test_closed_parent_output_does_not_interrupt_cleanup(self):
        process, event, _ = self.start(only_ready=True)
        self.assertEqual(event["event"], "ready")
        port = int((self.base / "port").read_text())
        process.stdout.close()
        # Force an error event through the closed pipe before stopping.
        process.stdin.write('{invalid}\n{"action":"stop"}\n')
        process.stdin.flush()
        self.assertEqual(process.wait(timeout=5), 0)
        self.assert_listener_closed(port)
        self.assertEqual(process.stderr.read(), "")

    def test_second_process_reports_identity_conflict(self):
        first, event, _ = self.start()
        self.assertEqual(event["event"], "ready")
        second, event, _ = self.start()
        self.assertEqual(event["event"], "error")
        self.assertIn("already running", event["message"])
        self.assertEqual(second.wait(timeout=5), 1)
        self.assertIsNone(first.poll())

    def test_port_conflict_reports_actionable_error(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            process, event, _ = self.start(occupied.getsockname()[1])
            self.assertEqual(event["event"], "error")
            self.assertIn("Port 53317 is in use", event["message"])
            self.assertEqual(process.wait(timeout=5), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
