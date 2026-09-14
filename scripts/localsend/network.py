"""LocalSend v2 TLS, bounded HTTP serving and IPv4 multicast discovery."""

import fcntl
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
from pathlib import Path
import re
import socket
from socketserver import ThreadingMixIn
import ssl
import struct
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit

from session import ProtocolError

PORT = 53317
GROUP = "224.0.0.167"
API = "/api/localsend/v2/"
MAX_JSON = 1024 * 1024


def local_address(address):
    ip = ipaddress.IPv4Address(address)
    return (ip.is_private or ip.is_link_local) and not (ip.is_unspecified or ip.is_multicast)


def peer_info(data, address):
    if not isinstance(data, dict) or not local_address(address):
        raise ValueError("Invalid device")
    alias, fingerprint = data.get("alias"), data.get("fingerprint")
    port = data.get("port", PORT)
    if (not isinstance(alias, str) or not alias.strip() or len(alias) > 100
            or any(ord(c) < 32 for c in alias)
            or not isinstance(fingerprint, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", fingerprint)
            or type(port) is not int or not 1 <= port <= 65535
            or data.get("protocol", "https") != "https"
            or not str(data.get("version", "")).startswith("2.")):
        raise ValueError("Device must use LocalSend v2 with encryption enabled")
    return {"id": f"{address}:{port}", "ip": address, "port": port, "alias": alias,
            "fingerprint": fingerprint.upper(), "protocol": "https",
            "deviceType": "mobile" if data.get("deviceType") == "mobile" else "desktop"}


def tls_identity(directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Also prevents two iNiR shells using the same identity and receive directory.
    lock = open(directory / "lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        raise RuntimeError("LocalSend is already running in another iNiR session") from None
    cert, key = directory / "certificate.pem", directory / "key.pem"
    if not cert.exists() or not key.exists():
        temporary_cert, temporary_key = directory / "certificate.new", directory / "key.new"
        try:
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256",
                            "-nodes", "-days", "3650", "-subj", "/CN=iNiR LocalSend",
                            "-keyout", str(temporary_key), "-out", str(temporary_cert)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
            temporary_key.chmod(0o600)
            temporary_cert.replace(cert)
            temporary_key.replace(key)
        finally:
            temporary_cert.unlink(missing_ok=True)
            temporary_key.unlink(missing_ok=True)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = ssl.TLSVersion.TLSv1_2
    server.load_cert_chain(cert, key)
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.check_hostname = False
    # LocalSend uses self-signed certificates. connect_peer pins the discovered
    # fingerprint before any metadata or file bytes are sent.
    client.verify_mode = ssl.CERT_NONE
    client.minimum_version = ssl.TLSVersion.TLSv1_2
    client.load_cert_chain(cert, key)
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    return server, client, hashlib.sha256(der).hexdigest().upper(), lock


def connect_peer(peer, context, timeout=10):
    if not local_address(peer["ip"]):
        raise ValueError("Device is not on a local network")
    connection = http.client.HTTPSConnection(peer["ip"], peer["port"], timeout=timeout, context=context)
    try:
        connection.connect()
        fingerprint = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest().upper()
        if fingerprint != peer["fingerprint"]:
            raise ValueError("Device identity changed. Refresh nearby devices and try again")
        return connection
    except BaseException:
        connection.close()
        raise


def probe_peer(address, context, own_info, port=PORT):
    """Explicit IP fallback; learn the certificate from this first TLS handshake."""
    if not local_address(address):
        raise ValueError("Enter a local IPv4 address")
    connection = http.client.HTTPSConnection(address, port, timeout=5, context=context)
    try:
        connection.connect()
        fingerprint = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest().upper()
        connection.request("POST", API + "register", json.dumps(own_info).encode(), {"Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read(MAX_JSON + 1)
        if response.status != 200 or len(body) > MAX_JSON:
            raise ValueError("No encrypted LocalSend device at that address")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("Invalid LocalSend device response")
        return {**data, "fingerprint": fingerprint, "port": port, "protocol": "https"}
    finally:
        connection.close()


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, context, owner):
        self.context = context
        self.owner = owner
        self.slots = threading.BoundedSemaphore(12)
        self.clients = set()
        self.clients_lock = threading.Lock()
        super().__init__(address, Handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        with self.clients_lock:
            self.clients.add(request)
        super().process_request(request, address)

    def process_request_thread(self, request, address):
        secured = None
        try:
            # Handshakes must not block the server accept loop.
            request.settimeout(10)
            secured = self.context.wrap_socket(request, server_side=True, do_handshake_on_connect=False)
            with self.clients_lock:
                self.clients.discard(request)
                self.clients.add(secured)
            secured.do_handshake()
            secured.settimeout(30)
            self.finish_request(secured, address)
        except (OSError, ValueError):
            pass
        finally:
            with self.clients_lock:
                self.clients.discard(request)
                self.clients.discard(secured)
            self.shutdown_request(secured or request)
            self.slots.release()

    def close_clients(self):
        with self.clients_lock:
            for client in self.clients:
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def drain(self):
        # All accepted requests hold a slot until their temporary files close.
        # Bound shutdown even if a filesystem or peer is unresponsive.
        deadline = time.monotonic() + 3
        for _ in range(12):
            if not self.slots.acquire(timeout=max(0, deadline - time.monotonic())):
                break


class Handler(BaseHTTPRequestHandler):
    # A fresh connection per request prevents unread bodies on rejected uploads
    # from being interpreted as a subsequent request.
    protocol_version = "HTTP/1.0"

    def log_message(self, *_):
        pass  # stdout is exclusively the JSON control channel; never log tokens.

    def reply(self, status, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        if self.headers.get("Transfer-Encoding"):
            raise ProtocolError(400, "JSON requires Content-Length")
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            raise ProtocolError(400, "Invalid content length") from None
        if not 0 < length <= MAX_JSON:
            raise ProtocolError(400, "Invalid metadata length")
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            raise ProtocolError(400, "Invalid JSON") from None
        if not isinstance(data, dict):
            raise ProtocolError(400, "Invalid JSON object")
        return data

    def do_GET(self):
        if urlsplit(self.path).path == API + "info":
            self.reply(200, self.server.owner.info)
        else:
            self.reply(404)

    def do_POST(self):
        try:
            owner = self.server.owner
            address = self.client_address[0]
            url = urlsplit(self.path)
            query = {key: values[0] for key, values in parse_qs(url.query).items() if len(values) == 1}
            if url.path == API + "register":
                owner.register(self.read_json(), address)
                self.reply(200, owner.info)
            elif url.path == API + "prepare-upload":
                self.reply(200, owner.prepare(self.read_json(), address))
            elif url.path == API + "upload":
                owner.receive(self, query, address)
                self.reply(200)
            elif url.path == API + "cancel":
                owner.remote_cancel(query.get("sessionId"), address)
                self.reply(200)
            else:
                self.reply(404)
        except ProtocolError as error:
            self.reply(error.status, {"message": str(error)})
        except (ValueError, KeyError, TypeError):
            self.reply(400, {"message": "Invalid request"})
        except OSError:
            # Disconnected client, timeout or storage failure; receive() has
            # already cleaned partial data and reported transfer errors.
            try:
                self.reply(500, {"message": "Transfer interrupted"})
            except OSError:
                pass


def interface_addresses():
    addresses = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        for _, name in socket.if_nameindex():
            try:
                raw = fcntl.ioctl(probe, 0x8915, struct.pack("256s", name.encode()[:15]))
                address = socket.inet_ntoa(raw[20:24])
                if local_address(address) and not address.startswith("127."):
                    addresses.append(address)
            except OSError:
                continue
    return addresses


class Discovery:
    def __init__(self, owner):
        self.owner = owner
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("", PORT))
        self.socket.settimeout(1)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        self.memberships = set()
        self.last_reply = 0
        self.send_lock = threading.Lock()

    def announce(self, response=False):
        with self.send_lock:
            addresses = set(interface_addresses())
            for address in self.memberships - addresses:
                try:
                    self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP,
                                           socket.inet_aton(GROUP) + socket.inet_aton(address))
                except OSError:
                    pass
                self.memberships.discard(address)
            for address in addresses - self.memberships:
                try:
                    self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                           socket.inet_aton(GROUP) + socket.inet_aton(address))
                    self.memberships.add(address)
                except OSError:
                    continue
            message = json.dumps({**self.owner.info, "announce": not response}).encode()
            for address in self.memberships:
                try:
                    self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
                    self.socket.sendto(message, (GROUP, PORT))
                except OSError:
                    continue

    def run(self):
        while not self.owner.stopping.is_set():
            try:
                data, (address, _) = self.socket.recvfrom(16384)
                message = json.loads(data)
                if self.owner.register(message, address) and message.get("announce") is True:
                    if time.monotonic() - self.last_reply > 1:
                        self.last_reply = time.monotonic()
                        self.announce(response=True)
            except (ValueError, TypeError, KeyError, OSError):
                continue
