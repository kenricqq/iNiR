"""One incoming or outgoing transfer at a time, owned independently of the UI."""

import secrets
import threading
import time

from storage import validate_files


class ProtocolError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Session:
    def __init__(self, direction, peer, files, clock=time.monotonic):
        self.id = secrets.token_hex(24)
        self.direction = direction
        self.peer = peer
        self.files = validate_files(files)
        self.tokens = {key: secrets.token_hex(32) for key in self.files}
        self.status = "pending" if direction == "receive" else "waiting"
        self.decision = threading.Event()
        self.cancelled = threading.Event()
        self.clock = clock
        self.updated = clock()
        self.progress = {key: 0 for key in self.files}
        self.inflight = set()
        self.completed = set()
        self.saved = []
        self.connections = set()
        self.message = ""

    def snapshot(self):
        return {"id": self.id, "direction": self.direction, "peer": self.peer["alias"],
                "status": self.status, "count": len(self.files),
                "names": [f["fileName"] for f in self.files.values()],
                "bytes": sum(self.progress.values()),
                "total": sum(f["size"] for f in self.files.values()),
                "message": self.message, "saved": self.saved[:]}

    @property
    def active(self):
        return self.status in ("pending", "waiting", "transferring")

    def decide(self, accept):
        if self.status != "pending":
            return
        self.status = "transferring" if accept else "declined"
        self.updated = self.clock()
        self.decision.set()

    def authorize(self, address, session_id, file_id, token):
        if (self.direction != "receive" or self.status != "transferring"
                or self.cancelled.is_set() or session_id != self.id
                or address != self.peer["ip"] or file_id not in self.tokens
                or not secrets.compare_digest(token, self.tokens[file_id])):
            raise ProtocolError(403, "Invalid session, token or sender")
        if file_id in self.inflight or file_id in self.completed:
            raise ProtocolError(409, "File already uploaded or in progress")
        self.inflight.add(file_id)
        self.updated = self.clock()
        return self.files[file_id]

    def finish_file(self, file_id, saved=None):
        self.inflight.discard(file_id)
        self.completed.add(file_id)
        if saved:
            self.saved.append(saved)
        self.updated = self.clock()
        if len(self.completed) == len(self.files):
            self.status = "complete"

    def cancel(self, status="cancelled", message=""):
        if not self.active:
            return
        self.status = status
        self.message = message
        self.cancelled.set()
        self.decision.set()
        # Interrupt blocking reads/writes so partial files are cleaned promptly.
        for connection in list(self.connections):
            try:
                connection.shutdown(2)
            except OSError:
                pass

    def expire(self):
        timeout = 120 if self.status == "pending" else 300
        if self.active and self.clock() - self.updated > timeout:
            self.cancel("expired", "Transfer timed out")
            return True
        return False
