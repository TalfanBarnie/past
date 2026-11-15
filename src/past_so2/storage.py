"""Backblaze B2 access through its S3-compatible API.

The private and public buckets use separate application keys, read from the
environment:

    B2_ENDPOINT                  e.g. https://s3.us-east-005.backblazeb2.com
    B2_BUCKET_PRIVATE            B2_PRIVATE_KEY_ID   B2_PRIVATE_APPLICATION_KEY
    B2_BUCKET_PUBLIC             B2_PUBLIC_KEY_ID    B2_PUBLIC_APPLICATION_KEY
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


class Bucket:
    def __init__(self, kind: str):
        if kind not in ("private", "public"):
            raise ValueError(kind)
        upper = kind.upper()
        names = ["B2_ENDPOINT", f"B2_BUCKET_{upper}", f"B2_{upper}_KEY_ID", f"B2_{upper}_APPLICATION_KEY"]
        missing = [n for n in names if not os.environ.get(n, "").strip()]
        if missing:
            raise RuntimeError("missing or empty settings: " + ", ".join(missing))
        endpoint = os.environ["B2_ENDPOINT"].strip()
        if not endpoint.startswith("http"):
            endpoint = "https://" + endpoint
        region = re.search(r"s3\.([a-z0-9-]+)\.backblazeb2\.com", endpoint)
        self.name = os.environ[f"B2_BUCKET_{upper}"].strip()
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region.group(1) if region else None,
            aws_access_key_id=os.environ[f"B2_{upper}_KEY_ID"],
            aws_secret_access_key=os.environ[f"B2_{upper}_APPLICATION_KEY"],
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 8, "mode": "adaptive"}),
        )

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.name, Key=key)
            return True
        except ClientError as err:
            if err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def upload(self, path: str | Path, key: str, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else None
        self.client.upload_file(str(path), self.name, key, ExtraArgs=extra)
        print(f"uploaded {key} ({Path(path).stat().st_size / 1e6:.1f} MB)", flush=True)

    def download(self, key: str, path: str | Path) -> Path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.name, key, str(path))
        return Path(path)

    def list(self, prefix: str = "") -> list[str]:
        keys = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.name, Prefix=prefix):
            keys += [o["Key"] for o in page.get("Contents", [])]
        return keys


@lru_cache
def bucket(kind: str) -> Bucket:
    return Bucket(kind)
