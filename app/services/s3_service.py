import hashlib
import os
import re
import tempfile
from datetime import datetime

from minio import Minio
from minio.error import S3Error

from app.config import settings

_client: Minio | None = None


def _get_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
            secure=False,  # HTTP, not HTTPS
        )
    return _client


def sanitize_filename(name: str) -> str:
    """ASCII only, no special chars, max 60 chars."""
    safe = re.sub(r'[^\x20-\x7E]', '_', name)
    safe = re.sub(r'[<>:"/\\|?*\s]+', '_', safe)
    return safe[:60] if len(safe) > 60 else safe


def _storage_id(user_id: int) -> int:
    """Opaque numeric ID for S3 paths. 10000000 = admin, +1 per user."""
    return user_id + 999


def generate_s3_key(original_name: str, user_id: int | None = None, dt: datetime | None = None) -> str:
    """Generate S3 key: files/[storage_id/]<year>/<month>/<day>/<hour>_<minute>_<second>_<name>"""
    if dt is None:
        dt = datetime.now()
    safe = sanitize_filename(original_name)
    prefix = f"{dt.year}/{dt.month:02d}/{dt.day:02d}"
    suffix = f"{dt.hour:02d}_{dt.minute:02d}_{dt.second:02d}_{safe}"
    if user_id is not None:
        return f"files/{_storage_id(user_id)}/{prefix}/{suffix}"
    return f"files/{prefix}/{suffix}"


def upload_file(local_path: str, s3_key: str) -> str | None:
    """Upload a local file to S3. Returns s3_key on success, None on failure."""
    try:
        client = _get_client()
        client.fput_object(settings.s3_bucket, s3_key, local_path)
        return s3_key
    except S3Error as e:
        print(f"[S3] upload error: {e}")
        return None


def download_file(s3_key: str) -> str | None:
    """Download file from S3 to a temp file. Returns temp path or None."""
    try:
        client = _get_client()
        # Create temp file in system temp dir
        suffix = os.path.splitext(s3_key)[1] or ".tmp"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        client.fget_object(settings.s3_bucket, s3_key, tmp_path)
        return tmp_path
    except S3Error as e:
        print(f"[S3] download error: {e}")
        return None


def delete_file(s3_key: str) -> bool:
    """Delete a file from S3. Returns True if successful."""
    try:
        client = _get_client()
        client.remove_object(settings.s3_bucket, s3_key)
        return True
    except S3Error as e:
        print(f"[S3] delete error: {e}")
        return False


def file_exists(s3_key: str) -> bool:
    """Check if an object exists in the bucket."""
    try:
        client = _get_client()
        client.stat_object(settings.s3_bucket, s3_key)
        return True
    except S3Error:
        return False
