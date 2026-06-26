import json
import os
import uuid

from fastapi import APIRouter, Query, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, get_current_user_optional
from app.models.user import User
from app.schemas.file import (
    ClearResponse,
    DeleteRequest,
    DeleteResponse,
    SyncRequest,
    SyncResponse,
)
from app.services import file_service, s3_service

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("")
async def list_files(
    page: int = 0,
    size: int = 20,
    start_date: str | None = None,
    end_date: str | None = None,
    file_type: str | None = Query(None, alias="type"),
    search: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    records = await file_service.get_user_files(
        db, user.id, page=page, size=size,
        start_date=start_date, end_date=end_date,
        file_type=file_type, search=search,
    )
    total = await file_service.count_user_files(
        db, user.id, start_date=start_date, end_date=end_date,
        file_type=file_type, search=search,
    )
    return {"files": records, "total": total}


@router.post("/sync", response_model=SyncResponse)
async def sync_file(
    req: SyncRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    data = req.model_dump(exclude_none=True)
    if "id" not in data or not data["id"]:
        data["id"] = uuid.uuid4().hex
    record = await file_service.create_file(db, user.id, data)
    return SyncResponse(success=True, id=record["id"])




@router.get("/{file_id}/thumbnail")
async def download_thumbnail(
    file_id: str,
    token: str | None = None,
    user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Return thumbnail for a file. Uses thumbS3Key from DB."""
    from app.services.file_service import _table_name as _tn
    from app.services.s3_service import _get_client
    from app.config import settings

    # Support JWT via query param
    if token and not user:
        from app.services.auth_service import decode_access_token
        p = decode_access_token(token)
        if p:
            uid = int(p.get("sub", 0))
            r = await db.execute(select(User).where(User.id == uid))
            user = r.scalar_one_or_none()

    # Search all user tables for the file
    records = []
    users_to_check = [user] if user and not getattr(user, "is_admin", False) else []
    if not users_to_check:
        r = await db.execute(select(User))
        users_to_check = list(r.scalars().all())

    for u in users_to_check:
        try:
            tbl = _tn(u.id)
            r = await db.execute(text(f"SELECT id, thumbS3Key, s3Key FROM {tbl} WHERE id = :id"), {"id": file_id})
            row = r.mappings().one_or_none()
            if row:
                d = dict(row)
                records.append(d)
                break
        except Exception:
            continue

    if not records:
        raise HTTPException(status_code=404, detail="Not found")
    target = records[0].get("thumbS3Key") or records[0].get("s3Key")
    if not target:
        raise HTTPException(status_code=404, detail="No thumb avail")

    client = _get_client()
    try:
        resp = await s3_service.async_s3(client.get_object, settings.s3_bucket, target)
        raw = resp.read() if hasattr(resp, 'read') else resp
        if not isinstance(raw, bytes):
            raw = raw.read() if hasattr(raw, 'read') else bytes(raw)
        from fastapi.responses import Response
        return Response(content=raw, media_type="image/jpeg",
                        headers={"Content-Disposition": "inline; filename=thumb.jpg"})
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Thumb fail: {e}")



import time

# In-memory store for multipart uploads
_multipart_uploads: dict[str, dict] = {}

@router.post("/upload/init")
async def init_multipart_upload(
    file_id: str = Form(...),
    name: str = Form(...),
    file_type: str = Form(alias="type"),
    mime_type: str | None = Form(None, alias="mimeType"),
    file_size: int = Form(...),
    user: User = Depends(get_current_user),
):
    from app.services.s3_service import _get_client
    from app.config import settings
    s3_key = s3_service.generate_s3_key(name, user_id=user.id)
    client = _get_client()
    minio_uid = client._create_multipart_upload(settings.s3_bucket, s3_key, {})
    upload_id = str(uuid.uuid4())
    _multipart_uploads[upload_id] = {
        "user_id": user.id,
        "file_id": file_id,
        "name": name,
        "type": file_type,
        "mime_type": mime_type,
        "file_size": file_size,
        "s3_key": s3_key,
        "minio_uid": minio_uid,
        "parts": [],
    }
    return {"upload_id": upload_id, "s3_key": s3_key, "part_size": 5 * 1024 * 1024}


@router.post("/upload/{upload_id}/part")
async def upload_part(
    upload_id: str,
    part_number: int = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    meta = _multipart_uploads.get(upload_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Upload not found")
    if meta["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Not your upload")

    from app.services.s3_service import _get_client
    from app.config import settings

    content = await file.read()
    client = _get_client()
    try:
        etag = await s3_service.async_s3(
            client._upload_part, settings.s3_bucket, meta["s3_key"], content, {}, meta["minio_uid"], part_number,
        )
        from minio.datatypes import Part
        meta["parts"].append(Part(part_number, etag))
        return {"part_number": part_number, "etag": etag, "success": True}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Part failed: {e}")


@router.post("/upload/{upload_id}/complete")
async def complete_multipart_upload(
    upload_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meta = _multipart_uploads.get(upload_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Upload not found")
    if meta["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Not your upload")

    from app.services.s3_service import _get_client
    from app.config import settings

    client = _get_client()
    sorted_parts = sorted(meta["parts"], key=lambda p: p.part_number)
    try:
        await s3_service.async_s3(
            client._complete_multipart_upload, settings.s3_bucket, meta["s3_key"], meta["minio_uid"], sorted_parts,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Complete failed: {e}")

    s3_key = meta["s3_key"]
    file_id = meta["file_id"]
    await file_service.update_file_s3(
        db, user.id, file_id, s3_key, meta["file_size"], meta["mime_type"],
    )
    del _multipart_uploads[upload_id]
    return {"success": True, "id": file_id, "s3Key": s3_key, "fileSize": meta["file_size"]}



@router.post("/upload/{upload_id}/abort")
async def abort_multipart_upload(
    upload_id: str,
    user: User = Depends(get_current_user),
):
    meta = _multipart_uploads.get(upload_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Upload not found")
    if meta["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Not your upload")
    from app.services.s3_service import _get_client
    from app.config import settings
    client = _get_client()
    try:
        await s3_service.async_s3(client._abort_multipart_upload, settings.s3_bucket, meta["s3_key"], meta["minio_uid"])
    except Exception:
        pass
    del _multipart_uploads[upload_id]
    return {"success": True}

@router.get("/upload/{upload_id}/parts")
async def list_upload_parts(
    upload_id: str,
    user: User = Depends(get_current_user),
):
    meta = _multipart_uploads.get(upload_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Upload not found")
    if meta["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Not your upload")
    return {"parts": [{"part_number": p.part_number, "etag": p.etag} for p in meta["parts"]]}

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    file_id: str = Form(...),
    name: str = Form(...),
    file_type: str = Form(alias="type"),
    mime_type: str | None = Form(None, alias="mimeType"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    suffix = os.path.splitext(name)[1] or ".tmp"
    tmp_path = f"/tmp/idea_king_{uuid.uuid4().hex}{suffix}"
    content = await file.read()
    with open(tmp_path, "wb") as f:
        f.write(content)
    file_size = len(content)

    s3_key = s3_service.generate_s3_key(name, user_id=user.id)
    uploaded = await s3_service.upload_file_async(tmp_path, s3_key)

    try:
        os.remove(tmp_path)
    except OSError:
        pass

    if not uploaded:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="S3 upload failed",
        )

    await file_service.update_file_s3(db, user.id, file_id, s3_key, file_size, mime_type)

    return {
        "success": True,
        "id": file_id,
        "s3Key": s3_key,
        "fileSize": file_size,
    }


@router.get("/{file_id}/download")
async def download_file(
    file_id: str,
    request: Request,
    token: str | None = None,
    user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Download a file from S3 through the API."""
    # Support JWT via query parameter (for external apps/browsers)
    if token and not user:
        from app.services.auth_service import decode_access_token
        p = decode_access_token(token)
        if p:
            uid = int(p.get("sub", 0))
            r = await db.execute(select(User).where(User.id == uid))
            user = r.scalar_one_or_none()

    import sys
    print(f"[download] token_set={token is not None} user_set={user is not None}", file=sys.stderr)
    # Find record from shared table or per-user table
    # For simplicity, query shared table first (has user_id)
    record = None
    s3_key = None
    file_name = None
    mime_type = None

    # Try per-user tables via admin or owner
    if user:
        user_records = await file_service.get_user_files(db, user.id)
        for r in user_records:
            if r["id"] == file_id:
                record = r
                break
    # Admin: search all user tables
    if not record and user and user.is_admin:
        from app.services.file_service import _table_name as _tn
        r = await db.execute(select(User))
        for u in r.scalars().all():
            try:
                tbl = _tn(u.id)
                r2 = await db.execute(text(f"SELECT * FROM {tbl} WHERE id = :id"), {"id": file_id})
                row = r2.mappings().one_or_none()
                if row:
                    record = dict(row)
                    break
            except Exception:
                continue

    if not record and request.session.get("admin_user_id"):
        admin_id = request.session["admin_user_id"]
        admin_records = await file_service.get_user_files(db, admin_id)
        for r in admin_records:
            if r["id"] == file_id:
                record = r
                break

    # Fallback: shared table
    if not record:
        result = await db.execute(
            text("SELECT * FROM files WHERE id = :id"),
            {"id": file_id},
        )
        row = result.mappings().one_or_none()
        if row:
            record = dict(row)

    if not record or not record.get("s3Key"):
        raise HTTPException(status_code=404, detail="File not found")

    # Auth check
    is_authorized = False
    if user and any(r["id"] == file_id for r in (await file_service.get_user_files(db, user.id))):
        is_authorized = True
    elif user and user.is_admin:
        is_authorized = True
    elif request.session.get("admin_user_id"):
        is_authorized = True

    if not is_authorized:
        raise HTTPException(status_code=403, detail="Access denied")

    tmp_path = await s3_service.download_file_async(record["s3Key"])
    if not tmp_path:
        raise HTTPException(status_code=502, detail="S3 download failed")

    media_type = record.get("mimeType") or "application/octet-stream"
    is_media = media_type and (media_type.startswith("image/") or media_type.startswith("video/") or media_type.startswith("audio/"))
    disp = "inline" if is_media else "attachment"
    nm = record.get("name", "file")
    from urllib.parse import quote
    nm_ascii = nm.encode("ascii", errors="replace").decode("ascii")
    hdr = {"Content-Disposition": disp + "; filename="" + nm_ascii + ""; filename*=UTF-8''" + quote(nm)}
    return FileResponse(tmp_path, media_type=media_type, filename=nm, headers=hdr)


@router.post("/delete", response_model=DeleteResponse)
async def delete_file(
    req: DeleteRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    deleted = await file_service.delete_user_file(db, req.id, user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found or not yours",
        )
    return DeleteResponse(success=True)


@router.post("/clear", response_model=ClearResponse)
async def clear_files(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    count = await file_service.clear_user_files(db, user.id)
    return ClearResponse(
        success=True, deleted_count=count
    )
