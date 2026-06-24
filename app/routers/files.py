import json
import os
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import text
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
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    records = await file_service.get_user_files(db, user.id)
    return {"files": records}


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
    uploaded = s3_service.upload_file(tmp_path, s3_key)

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
    elif request.session.get("admin_user_id"):
        is_authorized = True

    if not is_authorized:
        raise HTTPException(status_code=403, detail="Access denied")

    tmp_path = s3_service.download_file(record["s3Key"])
    if not tmp_path:
        raise HTTPException(status_code=502, detail="S3 download failed")

    media_type = record.get("mimeType") or "application/octet-stream"
    return FileResponse(tmp_path, media_type=media_type, filename=record.get("name", "file"))


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
