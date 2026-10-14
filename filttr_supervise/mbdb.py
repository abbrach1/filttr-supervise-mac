"""Minimal writer for Apple's legacy Manifest.mbdb backup index format.

The MBDB binary layout is a long-standing, publicly documented part of iTunes /
MobileBackup2 backups (magic ``mbdb\\x05\\x00``, length-prefixed strings, etc.).
This module only *writes* records needed for a one-file synthetic restore; it is
not a full backup library.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field


def _write_string(value: str) -> bytes:
    if not value:
        return b"\xff\xff"
    encoded = value.encode("utf-8")
    return struct.pack(">H", len(encoded)) + encoded


def _write_data(value: bytes) -> bytes:
    if not value:
        return b"\xff\xff"
    return struct.pack(">H", len(value)) + value


@dataclass
class MbdbRecord:
    domain: str
    filename: str
    link: str = ""
    hash: bytes = b""
    key: bytes = b""
    mode: int = 0o100644
    unknown2: int = 0
    unknown3: int = 0
    inode: int = 0
    user_id: int = 0
    group_id: int = 0
    mtime: int = 0
    atime: int = 0
    ctime: int = 0
    size: int = 0
    flags: int = 4
    properties: list[tuple[str, str]] = field(default_factory=list)

    def to_bytes(self) -> bytes:
        out = bytearray()
        out += _write_string(self.domain)
        out += _write_string(self.filename)
        out += _write_string(self.link)
        out += _write_data(self.hash)
        out += _write_data(self.key)
        out += struct.pack(">H", self.mode & 0xFFFF)
        out += struct.pack(">I", self.unknown2 & 0xFFFFFFFF)
        out += struct.pack(">I", self.unknown3 & 0xFFFFFFFF)
        out += struct.pack(">Q", self.inode & 0xFFFFFFFFFFFFFFFF)
        out += struct.pack(">I", self.user_id & 0xFFFFFFFF)
        out += struct.pack(">I", self.group_id & 0xFFFFFFFF)
        out += struct.pack(">I", self.mtime & 0xFFFFFFFF)
        out += struct.pack(">I", self.atime & 0xFFFFFFFF)
        out += struct.pack(">I", self.ctime & 0xFFFFFFFF)
        out += struct.pack(">Q", self.size & 0xFFFFFFFFFFFFFFFF)
        out += struct.pack(">B", self.flags & 0xFF)
        out += struct.pack(">B", len(self.properties) & 0xFF)
        for key, value in self.properties:
            out += _write_string(key)
            out += _write_string(value)
        return bytes(out)


@dataclass
class Mbdb:
    records: list[MbdbRecord]

    def to_bytes(self) -> bytes:
        body = b"".join(record.to_bytes() for record in self.records)
        return b"mbdb\x05\x00" + body


# File type bits commonly OR'd into mode.
S_IFREG = 0o100000
S_IFDIR = 0o040000

DEFAULT_FILE_MODE = S_IFREG | 0o755
DEFAULT_DIR_MODE = S_IFDIR | 0o755
