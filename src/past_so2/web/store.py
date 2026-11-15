"""Where the web builders read from and write to: the buckets, or local folders for testing."""

from __future__ import annotations

import gzip
from pathlib import Path


class Source:
    """Read-only view of the private bucket, or of a local folder with the same layout."""

    def __init__(self, local: Path | None = None):
        self.local = local
        if local is None:
            from .. import storage

            self.bucket = storage.bucket("private")

    def read(self, key: str) -> bytes:
        return (self.local / key).read_bytes() if self.local else self.bucket.get_bytes(key)

    def exists(self, key: str) -> bool:
        return (self.local / key).exists() if self.local else self.bucket.exists(key)

    def list(self, prefix: str) -> list[str]:
        if self.local is None:
            return self.bucket.list(prefix)
        return sorted(str(p.relative_to(self.local)) for p in self.local.glob(prefix + "**/*") if p.is_file())

    def download(self, key: str, path: Path) -> Path:
        if self.local is None:
            return self.bucket.download(key, path)
        return self.local / key

    def write(self, key: str, data: bytes) -> None:
        if self.local is None:
            self.bucket.put_bytes(key, data, "application/octet-stream")
            return
        (self.local / key).parent.mkdir(parents=True, exist_ok=True)
        (self.local / key).write_bytes(data)


class Sink:
    """The public bucket under a prefix, or a local folder."""

    def __init__(self, prefix: str, local: Path | None = None):
        self.prefix, self.local = prefix.strip("/"), local
        if local is None:
            from .. import storage

            self.bucket = storage.bucket("public")

    def write(self, name: str, data: bytes, content_type: str, compress: bool = False, max_age: int = 300) -> None:
        if self.local is None:
            self.bucket.put_bytes(f"{self.prefix}/{name}", data, content_type, compress, max_age)
            return
        path = self.local / self.prefix / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, name: str) -> bytes | None:
        if self.local is None:
            key = f"{self.prefix}/{name}"
            if not self.bucket.exists(key):
                return None
            data = self.bucket.get_bytes(key)
            return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
        path = self.local / self.prefix / name
        return path.read_bytes() if path.exists() else None

    def list(self, sub: str) -> list[str]:
        if self.local is None:
            return [k[len(self.prefix) + 1 :] for k in self.bucket.list(f"{self.prefix}/{sub}")]
        root = self.local / self.prefix
        return sorted(str(p.relative_to(root)) for p in root.glob(sub + "**/*") if p.is_file())
