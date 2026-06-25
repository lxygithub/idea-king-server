import os
import subprocess
import tempfile
from io import BytesIO

from PIL import Image

from app.services.s3_service import upload_file as s3_upload


THUMB_MAX_WIDTH = 300
THUMB_MAX_HEIGHT = 300
THUMB_QUALITY = 75


def _thumb_s3_key(storage_id: int, file_id: str) -> str:
    """S3 key for thumbnail."""
    return f"thumbnails/{storage_id}/{file_id}.jpg"


async def generate_and_upload_thumbnail(
    s3_key: str,
    storage_id: int,
    file_id: str,
    file_type: str,
    mime_type: str | None = None,
) -> str | None:
    """Download original from S3, generate thumbnail, upload to S3.
    Returns thumbS3Key on success, None on failure."""
    from app.services.s3_service import _get_client
    from app.config import settings

    client = _get_client()
    thumb_key = _thumb_s3_key(storage_id, file_id)

    try:
        if file_type == "image" or (mime_type and mime_type.startswith("image/")):
            return await _image_thumbnail(client, s3_key, thumb_key)
        elif file_type == "video" or (mime_type and mime_type.startswith("video/")):
            return await _video_thumbnail(client, s3_key, thumb_key)
    except Exception as e:
        print(f"[thumbnail] generation failed for {file_id}: {e}")
        return None


async def _image_thumbnail(client, s3_key: str, thumb_key: str) -> str | None:
    """Generate thumbnail for image using Pillow."""
    from app.config import settings

    # Download original
    resp = client.get_object(settings.s3_bucket, s3_key)
    img_data = resp if isinstance(resp, bytes) else resp.read()

    # Resize
    img = Image.open(BytesIO(img_data))
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    img.thumbnail((THUMB_MAX_WIDTH, THUMB_MAX_HEIGHT), Image.LANCZOS)

    # Save to bytes
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=THUMB_QUALITY)
    buf.seek(0)

    # Upload
    client.put_object(
        settings.s3_bucket,
        thumb_key,
        buf,
        length=buf.getbuffer().nbytes,
        content_type="image/jpeg",
    )
    return thumb_key


async def _video_thumbnail(client, s3_key: str, thumb_key: str) -> str | None:
    """Extract a frame from video using ffmpeg."""
    from app.config import settings

    # Download original to temp file
    resp = client.get_object(settings.s3_bucket, s3_key)
    raw = resp if isinstance(resp, bytes) else resp.read()
    if not isinstance(raw, bytes):
        raw = raw.read() if hasattr(raw, 'read') else bytes(raw)
    with tempfile.NamedTemporaryFile(suffix=".video", delete=False) as tmp_in:
        tmp_in.write(raw)
        tmp_in_path = tmp_in.name

    tmp_out_path = tmp_in_path + ".jpg"
    try:
        # Use ffmpeg to extract a frame at 1 second, resize to max 300px
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "00:00:01", "-i", tmp_in_path,
             "-vframes", "1", "-vf", f"scale='min({THUMB_MAX_WIDTH},iw)':min'({THUMB_MAX_HEIGHT},ih)':force_original_aspect_ratio=decrease",
             "-q:v", "2", tmp_out_path],
            capture_output=True, timeout=30,
        )

        if not os.path.exists(tmp_out_path):
            return None

        with open(tmp_out_path, "rb") as f:
            data = f.read()

        # Upload to S3
        client.put_object(
            settings.s3_bucket,
            thumb_key,
            data,
            length=len(data),
            content_type="image/jpeg",
        )
        return thumb_key
    finally:
        for p in [tmp_in_path, tmp_out_path]:
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except OSError:
                pass
