#!/usr/bin/env python3
"""Session-scoped LocalSend endpoint. JSON lines on stdin/stdout, no local HTTP API.

Commands: stop, refresh, decide {id, accept}, cancel {id}, send {peer, paths, pin}.
Events: ready, peers, transfer, error. All network listeners die with this process.
"""

import argparse
from contextlib import ExitStack
from http.client import HTTPException
import json
import mimetypes
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlencode, urlsplit

from network import API, MAX_JSON, PORT, Discovery, Server, connect_peer, peer_info, probe_peer, tls_identity
from session import ProtocolError, Session
from storage import MAX_FILES, ReceiveFile


def upload_chunks(handler, expected):
    """Read fixed-length or HTTP/1.1 chunked uploads with an exact byte budget."""
    encoding = handler.headers.get("Transfer-Encoding", "").lower()
    length = handler.headers.get("Content-Length")
    if encoding:
        if encoding != "chunked" or length is not None:
            raise ProtocolError(400, "Invalid upload framing")
        total = 0
        while True:
            line = handler.rfile.readline(128)
            if not line.endswith(b"\r\n"):
                raise ProtocolError(400, "Invalid chunk")
            try:
                size = int(line.strip().split(b";", 1)[0], 16)
            except ValueError:
                raise ProtocolError(400, "Invalid chunk size") from None
            if size < 0 or total + size > expected:
                raise ProtocolError(400, "Upload exceeds declared size")
            if size == 0:
                if handler.rfile.read(2) != b"\r\n" or total != expected:
                    raise ProtocolError(400, "Incomplete upload")
                return
            remaining = size
            while remaining:
                block = handler.rfile.read(min(65536, remaining))
                if not block:
                    raise ProtocolError(400, "Incomplete upload")
                remaining -= len(block)
                total += len(block)
                yield block
            if handler.rfile.read(2) != b"\r\n":
                raise ProtocolError(400, "Invalid chunk ending")
    else:
        if length is None or not length.isdecimal() or int(length) != expected:
            raise ProtocolError(400, "Upload size differs from accepted size")
        remaining = expected
        while remaining:
            block = handler.rfile.read(min(65536, remaining))
            if not block:
                raise ProtocolError(400, "Incomplete upload")
            remaining -= len(block)
            yield block


class Bridge:
    def __init__(self, directory, emit, fingerprint="", client_context=None, alias=None):
        self.directory = Path(directory)
        self.emit = emit
        self.client_context = client_context
        self.info = {"alias": (alias or socket.gethostname())[:100], "version": "2.1",
                     "deviceModel": "iNiR", "deviceType": "desktop", "fingerprint": fingerprint,
                     "port": PORT, "protocol": "https", "download": False}
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.peers = {}
        self.session = None
        self.discovery = None
        self.last_progress = 0
        self.probing = False

    def publish(self):
        if self.session:
            self.emit({"event": "transfer", "transfer": self.session.snapshot()})

    def register(self, data, address):
        peer = peer_info(data, address)
        if peer["fingerprint"] == self.info["fingerprint"]:
            return False
        with self.lock:
            if self.stopping.is_set():
                return False
            old = self.peers.get(peer["id"])
            if old is None and len(self.peers) >= 256:
                return False
            self.peers[peer["id"]] = (peer, time.monotonic())
            if old is None or old[0] != peer:
                self.publish_peers()
        return True

    def publish_peers(self):
        self.emit({"event": "peers", "peers": sorted(
            [peer for peer, _ in self.peers.values()], key=lambda p: p["alias"].casefold())})

    def prepare(self, data, address):
        peer = peer_info(data.get("info"), address)
        with self.lock:
            if self.stopping.is_set() or (self.session and self.session.active):
                raise ProtocolError(409, "Another transfer is active")
            session = Session("receive", peer, data.get("files"))
            self.session = session
            self.publish()
        # The response is held until the local user explicitly accepts.
        session.decision.wait(121)
        with self.lock:
            if session.status == "pending":
                session.cancel("expired", "Request timed out")
                self.publish()
            if session.status != "transferring" or self.stopping.is_set():
                raise ProtocolError(403, "Transfer declined or expired")
            return {"sessionId": session.id, "files": session.tokens.copy()}

    def decide(self, session_id, accept):
        with self.lock:
            if self.session and self.session.id == session_id:
                if accept:
                    self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                    size = sum(f["size"] for f in self.session.files.values())
                    if shutil.disk_usage(self.directory).free < size:
                        self.session.cancel("failed", "Not enough space in Downloads")
                        self.publish()
                        return
                self.session.decide(accept)
                self.publish()

    def progress(self, session, file_id, amount):
        with self.lock:
            if session.cancelled.is_set() or self.stopping.is_set():
                raise ProtocolError(409, "Transfer cancelled")
            session.progress[file_id] = amount
            session.updated = time.monotonic()
            if session.updated - self.last_progress > 0.2:
                self.last_progress = session.updated
                self.publish()

    def receive(self, handler, query, address):
        file_id = query.get("fileId", "")
        with self.lock:
            session = self.session
            if not session:
                raise ProtocolError(403, "No accepted transfer")
            metadata = session.authorize(address, query.get("sessionId"), file_id, query.get("token", ""))
            session.connections.add(handler.connection)
        try:
            with ReceiveFile(self.directory, metadata["fileName"]) as target:
                amount = 0
                for block in upload_chunks(handler, metadata["size"]):
                    self.progress(session, file_id, amount)
                    target.write(block)
                    amount += len(block)
                with self.lock:
                    self.progress(session, file_id, amount)
                    try:
                        saved = target.commit(metadata["sha256"])
                    except ValueError as error:
                        raise ProtocolError(422, str(error)) from error
                    session.finish_file(file_id, saved)
                    self.publish()
        except (OSError, ValueError, ProtocolError) as error:
            with self.lock:
                # Keep this response usable while interrupting concurrent files.
                session.connections.discard(handler.connection)
                session.cancel("failed", str(error))
                self.publish()
            raise
        finally:
            with self.lock:
                session.connections.discard(handler.connection)

    def remote_cancel(self, session_id, address):
        with self.lock:
            if (not self.session or self.session.direction != "receive"
                    or self.session.id != session_id or self.session.peer["ip"] != address):
                raise ProtocolError(403, "Invalid session or sender")
            self.session.cancel()
            self.publish()

    def cancel(self, session_id):
        with self.lock:
            if self.session and self.session.id == session_id:
                self.session.cancel()
                self.publish()

    def request(self, peer, route, data=None, session=None, stream=None, file_id=None):
        connection = connect_peer(peer, self.client_context)
        connection.sock.settimeout(125 if route.startswith("prepare-upload") else 30)
        sock = connection.sock
        if session:
            with self.lock:
                if session.cancelled.is_set():
                    connection.close()
                    raise ProtocolError(409, "Transfer cancelled")
                session.connections.add(sock)
        try:
            if stream is None:
                body = json.dumps(data).encode() if data is not None else b""
                connection.request("POST", API + route, body, {"Content-Type": "application/json"})
            else:
                size = session.files[file_id]["size"]
                connection.putrequest("POST", API + route)
                connection.putheader("Content-Length", str(size))
                connection.putheader("Content-Type", "application/octet-stream")
                connection.endheaders()
                amount = 0
                while amount < size:
                    block = stream.read(min(65536, size - amount))
                    if not block:
                        raise ValueError("Selected file changed during transfer")
                    connection.send(block)
                    amount += len(block)
                    self.progress(session, file_id, amount)
            response = connection.getresponse()
            body = response.read(MAX_JSON + 1)
            if len(body) > MAX_JSON:
                raise ValueError("Device returned too much metadata")
            messages = {401: "Receiver requires a PIN. Enter it and send again.",
                        403: "Receiver declined the transfer", 409: "Receiver is busy",
                        429: "Receiver is busy. Try again later", 422: "Checksum mismatch"}
            if response.status not in (200, 204):
                raise ProtocolError(response.status, messages.get(response.status, f"Receiver error ({response.status})"))
            return json.loads(body) if body else None
        finally:
            if session:
                with self.lock:
                    session.connections.discard(sock)
            connection.close()

    def send(self, peer_id, paths, pin=""):
        with self.lock:
            if self.stopping.is_set() or (self.session and self.session.active):
                raise ValueError("Another transfer is active")
            if peer_id not in self.peers:
                raise ValueError("Device is no longer nearby. Refresh and try again")
            peer = self.peers[peer_id][0].copy()
            if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_FILES:
                raise ValueError("Choose between 1 and 1000 files")
            # Reserve the transfer before starting a worker to prevent double send.
            stack = ExitStack()
            try:
                files, streams = {}, {}
                for index, path in enumerate(paths):
                    if not isinstance(path, str):
                        raise ValueError("Invalid file selection")
                    if path.startswith("file:"):
                        url = urlsplit(path)
                        if url.netloc not in ("", "localhost"):
                            raise ValueError("Only local files can be sent")
                        path = unquote(url.path)
                    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
                    stream = stack.enter_context(os.fdopen(fd, "rb"))
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("Choose regular files; archive folders before sending")
                    key = str(index)
                    streams[key] = stream
                    files[key] = {"id": key, "fileName": Path(path).name, "size": info.st_size,
                                  "fileType": mimetypes.guess_type(path)[0] or "application/octet-stream"}
                session = Session("send", peer, files)
                self.session = session
                self.publish()
            except BaseException:
                stack.close()
                raise
        threading.Thread(target=self.send_worker, args=(session, files, streams, stack, pin), daemon=True).start()

    def send_worker(self, session, files, streams, stack, pin):
        remote_id = None
        try:
            route = "prepare-upload" + ("?" + urlencode({"pin": pin}) if pin else "")
            prepared = self.request(session.peer, route, {"info": self.info, "files": files}, session)
            if not isinstance(prepared, dict) or not isinstance(prepared.get("sessionId"), str):
                raise ValueError("Receiver returned an invalid session")
            remote_id = prepared["sessionId"]
            accepted = prepared.get("files")
            if (not remote_id or len(remote_id) > 512 or not isinstance(accepted, dict)
                    or not accepted or not set(accepted) <= set(files)
                    or any(not isinstance(token, str) or not token or len(token) > 512 for token in accepted.values())):
                raise ValueError("Receiver returned invalid file tokens")
            with self.lock:
                if session.cancelled.is_set():
                    raise ProtocolError(409, "Transfer cancelled")
                session.status = "transferring"
                self.publish()
            for file_id, token in accepted.items():
                query = urlencode({"sessionId": remote_id, "fileId": file_id, "token": token})
                self.request(session.peer, "upload?" + query, session=session,
                             stream=streams[file_id], file_id=file_id)
                with self.lock:
                    if session.cancelled.is_set():
                        raise ProtocolError(409, "Transfer cancelled")
                    session.finish_file(file_id)
                    self.publish()
            with self.lock:
                session.status = "complete"
                if len(accepted) < len(files):
                    session.message = f"Sent {len(accepted)} of {len(files)} files; receiver declined the rest"
                self.publish()
        except (OSError, ValueError, HTTPException, ProtocolError, KeyError, TypeError) as error:
            with self.lock:
                session.cancel("failed", str(error))
                self.publish()
            if remote_id:
                try:
                    self.request(session.peer, "cancel?" + urlencode({"sessionId": remote_id}))
                except Exception:
                    pass
        finally:
            stack.close()

    def command(self, command):
        action = command.get("action")
        if action == "stop":
            self.stop()
        elif action == "refresh":
            if self.discovery:
                self.discovery.announce()
        elif action == "discover":
            with self.lock:
                if not self.probing:
                    self.probing = True
                    threading.Thread(target=self.discover, args=(command.get("address"),), daemon=True).start()
        elif action == "decide":
            self.decide(command.get("id"), command.get("accept") is True)
        elif action == "cancel":
            self.cancel(command.get("id"))
        elif action == "send":
            self.send(command.get("peer"), command.get("paths"), command.get("pin", ""))
        else:
            raise ValueError("Unknown command")

    def discover(self, address):
        try:
            self.register(probe_peer(address, self.client_context, self.info), address)
        except (ValueError, OSError, HTTPException, TypeError) as error:
            self.emit({"event": "error", "message": str(error)})
        finally:
            with self.lock:
                self.probing = False

    def maintain(self):
        ticks = 0
        while not self.stopping.wait(1):
            with self.lock:
                if self.session and self.session.expire():
                    self.publish()
                expired = [key for key, (_, seen) in self.peers.items() if time.monotonic() - seen > 90]
                for key in expired:
                    del self.peers[key]
                if expired:
                    self.publish_peers()
            if ticks % 15 == 0 and self.discovery:
                self.discovery.announce()
            ticks += 1

    def stop(self):
        with self.lock:
            self.stopping.set()
            if self.session:
                self.session.cancel()
                self.publish()


def downloads_directory():
    try:
        path = subprocess.check_output(["xdg-user-dir", "DOWNLOAD"], text=True, timeout=3).strip()
        if path and Path(path).is_absolute():
            return Path(path) / "LocalSend"
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.home() / "Downloads" / "LocalSend"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    output_lock = threading.Lock()

    def emit(event):
        with output_lock:
            try:
                print(json.dumps(event), flush=True)
            except BrokenPipeError:
                # A dying parent closes both pipes. Reporting cancellation must
                # not interrupt the finally block that removes partial files.
                with open(os.devnull, "w") as sink:
                    os.dup2(sink.fileno(), sys.stdout.fileno())

    bridge = server = discovery = identity_lock = server_thread = None
    try:
        server_context, client_context, fingerprint, identity_lock = tls_identity(args.state_dir)
        bridge = Bridge(downloads_directory(), emit, fingerprint, client_context)
        server = Server(("", PORT), server_context, bridge)
        discovery = bridge.discovery = Discovery(bridge)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        threading.Thread(target=discovery.run, daemon=True).start()
        threading.Thread(target=bridge.maintain, daemon=True).start()

        def terminate(*_):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, terminate)
        signal.signal(signal.SIGINT, terminate)
        emit({"event": "ready", "alias": bridge.info["alias"], "directory": str(bridge.directory)})
        while not bridge.stopping.is_set():
            line = sys.stdin.readline(MAX_JSON + 1)
            if not line:
                break  # Parent exited: do not leave a discoverable daemon behind.
            try:
                if len(line) > MAX_JSON:
                    raise ValueError("Command too large")
                command = json.loads(line)
                if not isinstance(command, dict):
                    raise ValueError("Invalid command")
                bridge.command(command)
            except (ValueError, OSError, TypeError) as error:
                emit({"event": "error", "message": str(error)})
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        message = "Port 53317 is in use. Close the LocalSend app or another sharing session." if isinstance(error, OSError) and error.errno == 98 else str(error)
        emit({"event": "error", "message": message})
        return 1
    finally:
        if bridge:
            bridge.stop()
        if discovery:
            discovery.socket.close()
        if server:
            if server_thread:
                server.shutdown()
            server.close_clients()
            server.server_close()
            server.drain()
        if identity_lock:
            identity_lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
