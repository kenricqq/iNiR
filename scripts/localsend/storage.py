"""Untrusted LocalSend metadata and atomic, non-overwriting receive storage."""

import hashlib
import os
from pathlib import PurePosixPath
import re
import secrets

MAX_FILES = 1000
MAX_BYTES = 100 * 1024**3


def validate_files(files):
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("Select between 1 and 1000 files")
    result = {}
    for key, value in files.items():
        if not isinstance(key, str) or not key or len(key) > 256 or not isinstance(value, dict):
            raise ValueError("Invalid file metadata")
        name = value.get("fileName")
        size = value.get("size")
        checksum = value.get("sha256")
        if (not isinstance(name, str) or not name or len(name.encode()) > 4096
                or name.startswith("/") or "\\" in name
                or any(ord(c) < 32 or ord(c) == 127 for c in name)
                or any(p in ("", ".", "..") or len(p.encode()) > 240 for p in name.split("/"))
                or len(name.split("/")) > 32):
            raise ValueError("Unsafe file name")
        if type(size) is not int or not 0 <= size <= MAX_BYTES:
            raise ValueError("Invalid file size")
        if checksum is not None and (not isinstance(checksum, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", checksum)):
            raise ValueError("Invalid checksum")
        result[key] = {"id": key, "fileName": name, "size": size, "sha256": checksum}
    if sum(f["size"] for f in result.values()) > MAX_BYTES:
        raise ValueError("Transfer exceeds 100 GiB")
    return result


class ReceiveFile:
    """Walk directory descriptors without following symlinks; publish only on success.

    A hard link is the atomic no-replace operation. Neither another transfer nor
    an existing file (including a symlink) can be overwritten between name checks.
    """

    def __init__(self, directory, name):
        self.parent = None
        self.stream = None
        self.temporary = ".inir-part-" + secrets.token_hex(16)
        self.name = PurePosixPath(name).name
        self.prefix = str(PurePosixPath(name).parent)
        self.digest = hashlib.sha256()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        self.parent = os.open(directory, flags)
        try:
            for segment in name.split("/")[:-1]:
                try:
                    os.mkdir(segment, mode=0o700, dir_fd=self.parent)
                except FileExistsError:
                    pass
                child = os.open(segment, flags, dir_fd=self.parent)
                os.close(self.parent)
                self.parent = child
            fd = os.open(self.temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=self.parent)
            self.stream = os.fdopen(fd, "wb")
        except BaseException:
            self.close()
            raise

    def write(self, data):
        self.stream.write(data)
        self.digest.update(data)

    def commit(self, checksum=None):
        if checksum and self.digest.hexdigest().lower() != checksum.lower():
            raise ValueError("Checksum mismatch")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        stem, suffix = os.path.splitext(self.name)
        for index in range(10000):
            name = self.name if index == 0 else f"{stem} ({index}){suffix}"
            try:
                os.link(self.temporary, name, src_dir_fd=self.parent,
                        dst_dir_fd=self.parent, follow_symlinks=False)
                return str(PurePosixPath(self.prefix) / name)
            except FileExistsError:
                continue
        raise OSError("Too many files with the same name")

    def close(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        if self.parent is not None:
            try:
                os.unlink(self.temporary, dir_fd=self.parent)
            except FileNotFoundError:
                pass
            finally:
                os.close(self.parent)
                self.parent = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
