#!/usr/bin/env python3
"""MinIO backup/restore mirror (Phase 8, Task 8.8).

Replaces `mc mirror` — uses the minio Python SDK already in the project's
.venv so no extra binary is needed in the Docker image.

Usage (called by backup.sh / restore.sh):
  python minio_mirror.py backup  <local_dir>   # download bucket → local
  python minio_mirror.py restore <local_dir>   # upload local → bucket
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from minio import Minio
from minio.error import S3Error


def _client() -> tuple[Minio, str]:
    endpoint = os.environ["MINIO_ENDPOINT"]
    access_key = os.environ["MINIO_ACCESS_KEY"]
    secret_key = os.environ["MINIO_SECRET_KEY"]
    bucket = os.environ.get("MINIO_BUCKET", "modir-bills")
    secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
    client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
    return client, bucket


def backup(dest_dir: str) -> None:
    """Download every object in the bucket into dest_dir, preserving key paths."""
    client, bucket = _client()
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    objects = list(client.list_objects(bucket, recursive=True))
    if not objects:
        print(f"  (bucket '{bucket}' is empty — nothing to mirror)")
        return

    for obj in objects:
        target = dest / obj.object_name
        target.parent.mkdir(parents=True, exist_ok=True)
        client.fget_object(bucket, obj.object_name, str(target))
        print(f"  ← {obj.object_name}")

    print(f"  {len(objects)} object(s) mirrored to {dest}")


def restore(src_dir: str) -> None:
    """Upload every file from src_dir back into the bucket, preserving paths."""
    client, bucket = _client()
    src = Path(src_dir)

    if not src.exists():
        print(f"  ERROR: source directory not found: {src}", file=sys.stderr)
        sys.exit(1)

    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)

    files = [p for p in src.rglob("*") if p.is_file()]
    if not files:
        print(f"  (no files in {src} — nothing to restore)")
        return

    for f in files:
        object_name = str(f.relative_to(src)).replace("\\", "/")
        client.fput_object(bucket, object_name, str(f))
        print(f"  → {object_name}")

    print(f"  {len(files)} file(s) restored to bucket '{bucket}'")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("backup", "restore"):
        print("Usage: minio_mirror.py <backup|restore> <directory>", file=sys.stderr)
        sys.exit(1)

    cmd, directory = sys.argv[1], sys.argv[2]
    try:
        if cmd == "backup":
            backup(directory)
        else:
            restore(directory)
    except S3Error as exc:
        print(f"MinIO error: {exc}", file=sys.stderr)
        sys.exit(1)
