"""
Cloudflare R2 object storage for persistent portrait image hosting.

Uses the S3-compatible API via boto3. Images are uploaded after generation
and served via R2's public URL, giving permanent CDN links that survive
Railway restarts and work across devices/sessions.

Required env vars:
    R2_ACCOUNT_ID       — Cloudflare account ID
    R2_ACCESS_KEY_ID    — R2 API token access key
    R2_SECRET_ACCESS_KEY — R2 API token secret key
    R2_BUCKET_NAME      — Bucket name (default: pet-printables-portraits)
    R2_PUBLIC_URL       — Public bucket URL (e.g. https://portraits.pet-printables.com
                          or https://pub-<hash>.r2.dev)

If R2 env vars are not set, uploads are silently skipped and the system
falls back to Railway's local /preview/ URLs (ephemeral).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_client = None
_bucket_name: str = ""
_public_url: str = ""


def _is_configured() -> bool:
    """Check if R2 credentials are set."""
    return bool(
        os.environ.get("R2_ACCESS_KEY_ID")
        and os.environ.get("R2_SECRET_ACCESS_KEY")
        and os.environ.get("R2_ACCOUNT_ID")
    )


def _get_client():
    """Lazy-init the boto3 S3 client for R2."""
    global _client, _bucket_name, _public_url
    if _client is not None:
        return _client

    import boto3

    account_id = os.environ["R2_ACCOUNT_ID"]
    _bucket_name = os.environ.get("R2_BUCKET_NAME", "pet-printables-portraits")
    _public_url = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")

    _client = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    log.info("R2 storage initialized: bucket=%s public=%s", _bucket_name, _public_url)
    return _client


def upload_portrait(file_path: Path, key: Optional[str] = None) -> Optional[str]:
    """
    Upload a portrait image to R2 and return its public URL.

    Args:
        file_path: Local path to the PNG file
        key: Optional S3 key (defaults to portraits/<filename>)

    Returns:
        Public URL string, or None if R2 is not configured
    """
    if not _is_configured():
        return None

    try:
        client = _get_client()
        s3_key = key or f"portraits/{file_path.name}"

        client.upload_file(
            str(file_path),
            _bucket_name,
            s3_key,
            ExtraArgs={
                "ContentType": "image/png",
                "CacheControl": "public, max-age=2592000",  # 30 days
            },
        )

        public_url = f"{_public_url}/{s3_key}" if _public_url else None
        log.info("R2 upload: %s → %s", file_path.name, public_url or s3_key)
        return public_url

    except Exception as exc:
        log.warning("R2 upload failed for %s: %s", file_path.name, exc)
        return None


def upload_bytes(data: bytes, key: str, content_type: str = "image/png") -> Optional[str]:
    """
    Upload raw bytes to R2 and return the public URL.

    Args:
        data: Image bytes
        key: S3 key (e.g. portraits/abc123_watercolor_biscuit.png)
        content_type: MIME type

    Returns:
        Public URL string, or None if R2 is not configured
    """
    if not _is_configured():
        return None

    try:
        from io import BytesIO
        client = _get_client()

        client.upload_fileobj(
            BytesIO(data),
            _bucket_name,
            key,
            ExtraArgs={
                "ContentType": content_type,
                "CacheControl": "public, max-age=2592000",
            },
        )

        public_url = f"{_public_url}/{key}" if _public_url else None
        log.info("R2 upload (bytes): %s → %s", key, public_url or key)
        return public_url

    except Exception as exc:
        log.warning("R2 upload failed for %s: %s", key, exc)
        return None
