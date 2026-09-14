"""Run with: python3 -m unittest discover -s scripts/localsend/tests -v"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge import Bridge, upload_chunks
from network import API, GROUP, MAX_JSON, Discovery, Server, connect_peer, peer_info, probe_peer, tls_identity
from session import ProtocolError, Session
from storage import ReceiveFile, validate_files


def files(name="hello.txt", content=b"hello"):
    return {"f": {"id": "f", "fileName": name, "size": len(content),
                  "sha256": hashlib.sha256(content).hexdigest()}}


def info(alias="Phone", fingerprint="A" * 64):
    return {"alias": alias, "version": "2.1", "fingerprint": fingerprint,
            "protocol": "https", "port": 53317}


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for condition")


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_unsafe_paths(self):
        for name in ("/tmp/escape", "../escape", "a/../b", "./a", "a//b", "a/", "a\\b",
                     "", "bad\nname", "a\x00b", "a" * 241, "/", ".", ".."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_files(files(name))

    def test_invalid_sizes_and_checksums(self):
        for size in (-1, True, 1.5, "5", 101 * 1024**3):
            with self.subTest(size=size), self.assertRaises(ValueError):
                validate_files({"f": {"fileName": "a", "size": size}})
        with self.assertRaises(ValueError):
            validate_files({"f": {"fileName": "a", "size": 0, "sha256": "oops"}})

    def test_limits_and_empty_metadata(self):
        for value in ({}, [], None, {str(i): files()["f"] for i in range(1001)}):
            with self.assertRaises(ValueError):
                validate_files(value)

    def test_no_overwrite_and_unicode(self):
        for expected in ("héllo.txt", "héllo (1).txt"):
            with ReceiveFile(self.directory, "folder/héllo.txt") as target:
                target.write(b"hello")
                saved = target.commit(hashlib.sha256(b"hello").hexdigest())
            self.assertEqual(saved, "folder/" + expected)
            self.assertEqual((self.directory / saved).read_bytes(), b"hello")
        self.assertFalse(list(self.directory.rglob(".inir-part-*")))

    def test_partial_file_cleanup(self):
        with ReceiveFile(self.directory, "a.txt") as target:
            target.write(b"incomplete")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_checksum_failure_never_publishes(self):
        with self.assertRaises(ValueError):
            with ReceiveFile(self.directory, "a.txt") as target:
                target.write(b"corrupt")
                target.commit("0" * 64)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_symlink_parent_cannot_escape(self):
        outside = self.directory / "outside"
        outside.mkdir()
        receive = self.directory / "receive"
        receive.mkdir()
        (receive / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            ReceiveFile(receive, "link/escape")
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_destination_is_not_overwritten(self):
        outside = self.directory / "original"
        outside.write_text("original")
        (self.directory / "a.txt").symlink_to(outside)
        with ReceiveFile(self.directory, "a.txt") as target:
            target.write(b"new")
            self.assertEqual(target.commit(), "a (1).txt")
        self.assertEqual(outside.read_text(), "original")

    def test_concurrent_collisions_are_atomic(self):
        def write(index):
            with ReceiveFile(self.directory, "same") as target:
                target.write(str(index).encode())
                return target.commit()
        with ThreadPoolExecutor(max_workers=8) as pool:
            saved = list(pool.map(write, range(20)))
        self.assertEqual(len(set(saved)), 20)
        self.assertEqual({(self.directory / name).read_text() for name in saved}, {str(i) for i in range(20)})


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.session = Session("receive", {"ip": "192.168.1.2", "alias": "Phone"}, files(), lambda: self.now)

    def authorize(self, **changes):
        args = {"address": "192.168.1.2", "session_id": self.session.id,
                "file_id": "f", "token": self.session.tokens["f"]}
        args.update(changes)
        return self.session.authorize(**args)

    def test_cannot_upload_without_approval(self):
        with self.assertRaises(ProtocolError):
            self.authorize()
        self.session.decide(True)
        self.assertEqual(self.authorize()["fileName"], "hello.txt")

    def test_ip_session_file_and_token_are_bound(self):
        self.session.decide(True)
        for change in ({"address": "192.168.1.3"}, {"session_id": "wrong"}, {"file_id": "wrong"}, {"token": "wrong"}):
            with self.subTest(change=change), self.assertRaises(ProtocolError):
                self.authorize(**change)

    def test_duplicate_and_replay_rejected(self):
        self.session.decide(True)
        self.authorize()
        with self.assertRaises(ProtocolError):
            self.authorize()
        self.session.finish_file("f")
        with self.assertRaises(ProtocolError):
            self.authorize()

    def test_decline_and_cancel_are_terminal(self):
        self.session.decide(False)
        self.session.decide(True)
        self.assertEqual(self.session.status, "declined")
        self.assertTrue(self.session.decision.is_set())

    def test_pending_and_accepted_sessions_expire(self):
        self.now = 121
        self.assertTrue(self.session.expire())
        self.assertEqual(self.session.status, "expired")
        self.assertTrue(self.session.decision.is_set())
        self.session = Session("receive", {"ip": "192.168.1.2", "alias": "Phone"}, files(), lambda: self.now)
        self.session.decide(True)
        self.now += 301
        self.assertTrue(self.session.expire())

    def test_snapshot_does_not_expose_tokens(self):
        snapshot = json.dumps(self.session.snapshot())
        self.assertNotIn(self.session.tokens["f"], snapshot)


class DiscoveryTests(unittest.TestCase):
    def test_multicast_memberships_follow_network_changes(self):
        bridge = Bridge("/unused", lambda event: None, "A" * 64)
        udp = Mock()
        with patch("network.socket.socket", return_value=udp):
            discovery = Discovery(bridge)
        with patch("network.interface_addresses", return_value=["192.168.1.2"]):
            discovery.announce()
        packet, destination = udp.sendto.call_args.args
        self.assertEqual(destination, (GROUP, 53317))
        self.assertEqual(json.loads(packet)["fingerprint"], "A" * 64)
        self.assertTrue(json.loads(packet)["announce"])
        with patch("network.interface_addresses", return_value=["10.0.0.2"]):
            discovery.announce(response=True)
        self.assertEqual(discovery.memberships, {"10.0.0.2"})
        self.assertFalse(json.loads(udp.sendto.call_args.args[0])["announce"])
        self.assertTrue(any(call.args[1] == socket.IP_DROP_MEMBERSHIP for call in udp.setsockopt.call_args_list))

    def test_stale_peers_expire_without_restart(self):
        events = []
        bridge = Bridge("/unused", events.append)
        bridge.peers = {"old": ({"id": "old", "alias": "Old"}, 0),
                        "new": ({"id": "new", "alias": "New"}, 99)}
        with patch.object(bridge.stopping, "wait", side_effect=[False, True]), patch("bridge.time.monotonic", return_value=100):
            bridge.maintain()
        self.assertEqual(list(bridge.peers), ["new"])
        self.assertEqual(events[-1]["peers"][0]["id"], "new")

    def test_validate_encrypted_local_peer(self):
        self.assertEqual(peer_info(info(), "192.168.1.2")["id"], "192.168.1.2:53317")
        for data, address in (({**info(), "protocol": "http"}, "192.168.1.2"),
                              ({**info(), "port": "53317"}, "192.168.1.2"),
                              ({**info(), "fingerprint": "invalid"}, "192.168.1.2"),
                              (info(), "8.8.8.8"), (info(), "0.0.0.0"),
                              (info(), "224.0.0.167"), ([], "192.168.1.2")):
            with self.subTest(data=data, address=address), self.assertRaises(ValueError):
                peer_info(data, address)

    def test_self_discovery_deduplication_and_peer_bound(self):
        events = []
        bridge = Bridge("/unused", events.append, "A" * 64)
        self.assertFalse(bridge.register(info(), "192.168.1.2"))
        self.assertTrue(bridge.register(info(fingerprint="B" * 64), "192.168.1.2"))
        bridge.register(info(fingerprint="B" * 64), "192.168.1.2")
        self.assertEqual(len(events), 1)
        for i in range(1, 258):
            bridge.register(info(fingerprint="B" * 64), f"10.0.{i // 256}.{i % 256}")
        self.assertEqual(len(bridge.peers), 256)


class FramingTests(unittest.TestCase):
    def chunks(self, body, headers, size):
        return b"".join(upload_chunks(SimpleNamespace(rfile=io.BytesIO(body), headers=headers), size))

    def test_fixed_and_zero_length_upload(self):
        self.assertEqual(self.chunks(b"hello", {"Content-Length": "5"}, 5), b"hello")
        self.assertEqual(self.chunks(b"", {"Content-Length": "0"}, 0), b"")

    def test_chunked_upload(self):
        self.assertEqual(self.chunks(b"2\r\nhe\r\n3\r\nllo\r\n0\r\n\r\n", {"Transfer-Encoding": "chunked"}, 5), b"hello")

    def test_bad_framing_is_rejected(self):
        for body, headers, size in ((b"hi", {"Content-Length": "5"}, 5),
                                    (b"hello", {"Content-Length": "5"}, 4),
                                    (b"hello", {}, 5),
                                    (b"", {"Transfer-Encoding": "gzip"}, 0),
                                    (b"", {"Transfer-Encoding": "chunked", "Content-Length": "0"}, 0),
                                    (b"0\r\n\r\n", {"Transfer-Encoding": "chunked"}, 1),
                                    (b"9\r\n", {"Transfer-Encoding": "chunked"}, 1),
                                    (b"-1\r\n", {"Transfer-Encoding": "chunked"}, 1)):
            with self.subTest(body=body, headers=headers), self.assertRaises(ProtocolError):
                self.chunks(body, headers, size)


class EndpointTests(unittest.TestCase):
    """Real TLS server and clients on loopback, no LAN discovery or user files."""

    @classmethod
    def setUpClass(cls):
        cls.identity_temp = tempfile.TemporaryDirectory()
        cls.server_context, cls.client_context, cls.fingerprint, cls.identity_lock = tls_identity(cls.identity_temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.identity_lock.close()
        cls.identity_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.events = []
        self.bridge = Bridge(self.directory / "received", self.events.append, self.fingerprint, self.client_context)
        self.server = Server(("127.0.0.1", 0), self.server_context, self.bridge)
        self.port = self.server.server_address[1]
        self.bridge.info["port"] = self.port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.pool = ThreadPoolExecutor(max_workers=4)

    def tearDown(self):
        self.bridge.stop()
        self.server.shutdown()
        self.server.close_clients()
        self.server.server_close()
        self.server.drain()
        self.pool.shutdown(wait=True)
        self.temp.cleanup()

    def request(self, route, data=None, body=None, headers=None, method="POST"):
        connection = http.client.HTTPSConnection("127.0.0.1", self.port, context=self.client_context, timeout=3)
        try:
            connection.request(method, API + route, json.dumps(data).encode() if data is not None else body,
                               headers or {})
            response = connection.getresponse()
            result = response.read()
            return response.status, json.loads(result) if result else None
        finally:
            connection.close()

    def prepare(self, metadata=None, accept=True):
        future = self.pool.submit(self.request, "prepare-upload", {"info": info(), "files": metadata or files()})
        wait_for(lambda: self.bridge.session is not None and self.bridge.session.status == "pending")
        self.assertFalse(future.done(), "Approval must happen before tokens are issued")
        self.assertFalse(self.bridge.directory.exists())
        self.bridge.decide(self.bridge.session.id, accept)
        return future.result(timeout=3)

    def upload(self, prepared, content=b"hello", **changes):
        query = {"sessionId": prepared["sessionId"], "fileId": "f", "token": prepared["files"]["f"]}
        query.update(changes)
        return self.request("upload?" + urlencode(query), body=content)

    def test_tls_info_and_registration(self):
        self.assertEqual(self.request("info", method="GET")[1]["fingerprint"], self.fingerprint)
        self.assertEqual(self.request("register", info())[0], 200)
        self.assertEqual(len(self.bridge.peers), 1)

    def test_certificate_pinning(self):
        peer = peer_info({**info(fingerprint=self.fingerprint), "port": self.port}, "127.0.0.1")
        connect_peer(peer, self.client_context).close()
        peer["fingerprint"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "identity changed"):
            connect_peer(peer, self.client_context)

    def test_direct_ip_discovers_and_pins_the_tls_certificate(self):
        found = probe_peer("127.0.0.1", self.client_context, info(), self.port)
        self.assertEqual(found["fingerprint"], self.fingerprint)
        self.assertEqual(found["port"], self.port)
        with self.assertRaises(ValueError):
            probe_peer("https://example.com", self.client_context, info())

    def test_accept_upload_and_replay(self):
        status, prepared = self.prepare()
        self.assertEqual(status, 200)
        self.assertEqual(self.upload(prepared)[0], 200)
        self.assertEqual((self.bridge.directory / "hello.txt").read_bytes(), b"hello")
        self.assertEqual(self.bridge.session.status, "complete")
        self.assertEqual(self.upload(prepared)[0], 403)

    def test_decline_does_not_write(self):
        self.assertEqual(self.prepare(accept=False)[0], 403)
        self.assertFalse(self.bridge.directory.exists())

    def test_unknown_token_does_not_consume_session(self):
        _, prepared = self.prepare()
        self.assertEqual(self.upload(prepared, token="wrong")[0], 403)
        self.assertEqual(self.bridge.session.status, "transferring")
        self.assertEqual(self.upload(prepared)[0], 200)

    def test_checksum_failure_cleans_partial_file(self):
        _, prepared = self.prepare()
        self.assertEqual(self.upload(prepared, b"wrong")[0], 422)
        self.assertEqual(self.bridge.session.status, "failed")
        self.assertEqual(list(self.bridge.directory.iterdir()), [])

    def test_rejected_metadata_does_not_create_session(self):
        for metadata in (files("../bad"), {}, [], {"f": {"size": -1, "fileName": "bad"}}):
            self.assertEqual(self.request("prepare-upload", {"info": info(), "files": metadata})[0], 400)
            self.assertIsNone(self.bridge.session)

    def test_parallel_request_is_busy(self):
        pending = self.pool.submit(self.request, "prepare-upload", {"info": info(), "files": files()})
        wait_for(lambda: self.bridge.session is not None)
        self.assertEqual(self.request("prepare-upload", {"info": info(), "files": files()})[0], 409)
        self.bridge.decide(self.bridge.session.id, False)
        self.assertEqual(pending.result()[0], 403)

    def test_stop_releases_pending_approval(self):
        pending = self.pool.submit(self.request, "prepare-upload", {"info": info(), "files": files()})
        wait_for(lambda: self.bridge.session is not None)
        self.bridge.stop()
        self.assertEqual(pending.result()[0], 403)
        self.assertFalse(self.bridge.directory.exists())

    def test_cancel_endpoint_and_stale_ui_decision(self):
        _, prepared = self.prepare()
        self.assertEqual(self.request("cancel?sessionId=wrong")[0], 403)
        self.assertEqual(self.request("cancel?" + urlencode({"sessionId": prepared["sessionId"]}))[0], 200)
        self.bridge.decide(prepared["sessionId"], True)
        self.assertEqual(self.bridge.session.status, "cancelled")
        self.assertEqual(self.upload(prepared)[0], 403)

    def test_oversized_metadata_and_malformed_json(self):
        self.assertEqual(self.request("register", body=b"{oops")[0], 400)
        self.assertEqual(self.request("register", body=b"x", headers={"Content-Length": str(MAX_JSON + 1)})[0], 400)
        self.assertEqual(self.request("unknown")[0], 404)

    def test_full_outgoing_round_trip(self):
        sender = Bridge(self.directory / "sender", lambda event: None, "B" * 64, self.client_context)
        sender.register(self.bridge.info, "127.0.0.1")
        selected = self.directory / "original.bin"
        selected.write_bytes(os.urandom(200000))
        sender.send(f"127.0.0.1:{self.port}", [selected.as_uri()])
        wait_for(lambda: self.bridge.session is not None)
        self.bridge.decide(self.bridge.session.id, True)
        wait_for(lambda: sender.session.status == "complete", timeout=5)
        self.assertEqual((self.bridge.directory / selected.name).read_bytes(), selected.read_bytes())
        self.assertEqual(sender.session.snapshot()["bytes"], 200000)

    def test_send_rejects_special_files_and_unknown_peers(self):
        with self.assertRaisesRegex(ValueError, "no longer nearby"):
            self.bridge.send("missing", ["/tmp/file"])
        self.bridge.register(info(), "127.0.0.1")
        fifo = self.directory / "pipe"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "regular files"):
            self.bridge.send("127.0.0.1:53317", [str(fifo)])
        self.assertIsNone(self.bridge.session)

    def test_disk_full_rejects_approval(self):
        pending = self.pool.submit(self.request, "prepare-upload", {"info": info(), "files": files()})
        wait_for(lambda: self.bridge.session is not None)
        with patch("bridge.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            self.bridge.decide(self.bridge.session.id, True)
        self.assertEqual(pending.result()[0], 403)
        self.assertEqual(self.bridge.session.status, "failed")

    def test_interrupted_upload_removes_partial_data(self):
        _, prepared = self.prepare(files(content=b"x" * 200000))
        connection = http.client.HTTPSConnection("127.0.0.1", self.port, context=self.client_context, timeout=3)
        query = urlencode({"sessionId": prepared["sessionId"], "fileId": "f", "token": prepared["files"]["f"]})
        connection.putrequest("POST", API + "upload?" + query)
        connection.putheader("Content-Length", "200000")
        connection.endheaders()
        connection.send(b"x" * 65536)
        wait_for(lambda: bool(list(self.bridge.directory.rglob(".inir-part-*"))))
        connection.close()
        wait_for(lambda: self.bridge.session.status == "failed")
        wait_for(lambda: not list(self.bridge.directory.rglob(".inir-part-*")))
        self.assertFalse((self.bridge.directory / "hello.txt").exists())

    def test_cancel_during_upload_removes_partial_data(self):
        _, prepared = self.prepare(files(content=b"x" * 200000))
        connection = http.client.HTTPSConnection("127.0.0.1", self.port, context=self.client_context, timeout=3)
        query = urlencode({"sessionId": prepared["sessionId"], "fileId": "f", "token": prepared["files"]["f"]})
        connection.putrequest("POST", API + "upload?" + query)
        connection.putheader("Content-Length", "200000")
        connection.endheaders()
        connection.send(b"x" * 65536)
        wait_for(lambda: bool(list(self.bridge.directory.rglob(".inir-part-*"))))
        self.bridge.cancel(prepared["sessionId"])
        connection.close()
        wait_for(lambda: not list(self.bridge.directory.rglob(".inir-part-*")))
        self.assertEqual(self.bridge.session.status, "cancelled")
        self.assertFalse((self.bridge.directory / "hello.txt").exists())

    def test_chunked_upload_over_tls(self):
        _, prepared = self.prepare()
        query = urlencode({"sessionId": prepared["sessionId"], "fileId": "f", "token": prepared["files"]["f"]})
        self.assertEqual(self.request("upload?" + query, body=b"5\r\nhello\r\n0\r\n\r\n",
                                     headers={"Transfer-Encoding": "chunked"})[0], 200)
        self.assertEqual((self.bridge.directory / "hello.txt").read_bytes(), b"hello")

    def test_waiting_sender_can_cancel(self):
        sender = Bridge(self.directory / "sender", lambda event: None, "B" * 64, self.client_context)
        sender.register(self.bridge.info, "127.0.0.1")
        selected = self.directory / "original.txt"
        selected.write_text("hello")
        sender.send(f"127.0.0.1:{self.port}", [selected.as_uri()])
        wait_for(lambda: self.bridge.session is not None)
        sender.cancel(sender.session.id)
        self.assertEqual(sender.session.status, "cancelled")
        self.bridge.decide(self.bridge.session.id, False)
        wait_for(lambda: not sender.session.connections)

    def test_partial_acceptance_is_reported(self):
        sender = Bridge(self.directory / "sender", lambda event: None, "B" * 64, self.client_context)
        sender.register(self.bridge.info, "127.0.0.1")
        selected = self.directory / "original.txt"
        selected.write_text("hello")
        original_prepare = self.bridge.prepare

        def partial_prepare(data, address):
            data["files"] = {"0": data["files"]["0"]}
            return original_prepare(data, address)

        with patch.object(self.bridge, "prepare", partial_prepare):
            sender.send(f"127.0.0.1:{self.port}", [selected.as_uri(), selected.as_uri()])
            wait_for(lambda: self.bridge.session is not None)
            self.bridge.decide(self.bridge.session.id, True)
            wait_for(lambda: sender.session.status == "complete")
        self.assertIn("Sent 1 of 2", sender.session.message)

    def test_receiver_pin_failure_is_actionable(self):
        sender = Bridge(self.directory / "sender", lambda event: None, "B" * 64, self.client_context)
        sender.register(self.bridge.info, "127.0.0.1")
        selected = self.directory / "original.txt"
        selected.write_text("hello")
        with patch.object(self.bridge, "prepare", side_effect=ProtocolError(401, "PIN required")):
            sender.send(f"127.0.0.1:{self.port}", [selected.as_uri()])
            wait_for(lambda: sender.session.status == "failed")
        self.assertIn("PIN", sender.session.message)

    def test_malformed_http_response_fails_without_stuck_transfer(self):
        sender = Bridge(self.directory / "sender", lambda event: None, "B" * 64, self.client_context)
        sender.register(self.bridge.info, "127.0.0.1")
        selected = self.directory / "original.txt"
        selected.write_text("hello")
        with patch.object(sender, "request", side_effect=http.client.BadStatusLine("bad response")):
            sender.send(f"127.0.0.1:{self.port}", [selected.as_uri()])
            wait_for(lambda: sender.session.status == "failed")
        self.assertIn("bad response", sender.session.message)

    def test_incomplete_tls_handshake_does_not_block_other_clients(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=3):
            self.assertEqual(self.request("info", method="GET")[0], 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
