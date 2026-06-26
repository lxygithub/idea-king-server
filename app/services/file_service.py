import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _storage_id(user_id: int) -> int:
    return user_id


def _table_name(user_id: int) -> str:
    return f"files_{_storage_id(user_id)}"


# Columns shared between per-user tables and the shared table
_COLS = (
    "id", "name", "type", "localPath", "textContent", "sourceUri",
    "receivedAt", "mimeType", "fileSize", "s3Key", "uploadProgress",
    "uploadError", "uploadId", "uploadedParts", "tags", "description", "thumbS3Key"
)
_COLS_STR = ", ".join(_COLS)
_PLACEHOLDERS = ", ".join(f":{c}" for c in _COLS)

# ===== Schema =====

_CREATE_SQL = """CREATE TABLE IF NOT EXISTS {table} (
  id VARCHAR(64) PRIMARY KEY,
  name TEXT NOT NULL,
  type VARCHAR(50) NOT NULL,
  localPath TEXT,
  textContent LONGTEXT,
  sourceUri VARCHAR(2000),
  receivedAt VARCHAR(32),
  mimeType VARCHAR(100),
  fileSize BIGINT DEFAULT 0,
  s3Key VARCHAR(500),
  uploadProgress DOUBLE,
  uploadError TEXT,
  uploadId VARCHAR(255),
  uploadedParts TEXT,
  tags TEXT,
  description TEXT,
  thumbS3Key VARCHAR(500)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


async def ensure_table(db: AsyncSession, user_id: int):
    """Create per-user table if it doesn't exist."""
    await db.execute(text(_CREATE_SQL.format(table=_table_name(user_id))))


# ===== To dict helper =====

def row_to_dict(row) -> dict[str, Any]:
    """Convert a raw DB row (RowMapping) to the API response format."""
    d = dict(row)
    if d.get("receivedAt") and hasattr(d["receivedAt"], "isoformat"):
        d["receivedAt"] = d["receivedAt"].isoformat()
    # tags stored as JSON string in DB, Flutter expects List
    if "tags" in d and isinstance(d["tags"], str):
        try:
            d["tags"] = json.loads(d["tags"])
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
    return d


# ===== CRUD =====

async def get_user_files(
    db: AsyncSession, user_id: int, page: int = 0, size: int = 0,
    start_date: str | None = None, end_date: str | None = None,
    file_type: str | None = None, search: str | None = None,
) -> list[dict]:
    """Fetch files from per-user table, with optional pagination and filters."""
    await ensure_table(db, user_id)
    table = _table_name(user_id)
    try:
        sql = f"SELECT {_COLS_STR} FROM {table} WHERE 1=1"
        if start_date:
            sql += f" AND receivedAt >= '{start_date}T00:00:00'"
        if end_date:
            sql += f" AND receivedAt <= '{end_date}T23:59:59'"
        if file_type:
            types_list = [f"'{t.strip()}'" for t in file_type.split(",") if t.strip()]
            sql += f" AND type IN ({','.join(types_list)})"
        if search:
            sql += f" AND (name LIKE '%{search}%' OR description LIKE '%{search}%' OR tags LIKE '%{search}%')"
        sql += " ORDER BY receivedAt DESC"
        if size > 0:
            sql += f" LIMIT {size} OFFSET {page * size}"
        result = await db.execute(text(sql))
        return [row_to_dict(r) for r in result.mappings().all()]
    except Exception:
        return []


async def get_all_files(
    db: AsyncSession, user_id: int | None = None
) -> list[dict]:
    """Admin: fetch files for a specific user."""
    if user_id:
        return await get_user_files(db, user_id)
    return []


async def create_file(
    db: AsyncSession, user_id: int, data: dict
) -> dict:
    """Insert file into per-user table."""
    table = _table_name(user_id)
    await ensure_table(db, user_id)

    # Preserve thumbS3Key from existing record if not in incoming data
    if "thumbS3Key" not in data or data.get("thumbS3Key") is None:
        try:
            existing = await db.execute(
                text(f"SELECT thumbS3Key FROM {table} WHERE id = :id"),
                {"id": data.get("id", "")},
            )
            row = existing.mappings().one_or_none()
            if row and row.get("thumbS3Key"):
                data["thumbS3Key"] = row["thumbS3Key"]
        except Exception:
            pass

    # Parse receivedAt
    if "receivedAt" in data and isinstance(data["receivedAt"], str):
        data["receivedAt"] = data["receivedAt"]  # keep as string for VARCHAR

    cols = ", ".join(data.keys())
    vals = ", ".join(f":{k}" for k in data.keys())
    await db.execute(
        text(f"REPLACE INTO {table} ({cols}) VALUES ({vals})"),
        data,
    )
    return data


async def update_file_s3(
    db: AsyncSession, user_id: int, file_id: str, s3_key: str, file_size: int, mime_type: str | None
):
    """Update s3Key + fileSize on an existing file."""
    await ensure_table(db, user_id)
    table = _table_name(user_id)
    await db.execute(
        text(f"UPDATE {table} SET s3Key = :k, fileSize = :sz, mimeType = :mt WHERE id = :id"),
        {"k": s3_key, "sz": file_size, "mt": mime_type, "id": file_id},
    )


async def delete_user_file(
    db: AsyncSession, file_id: str, user_id: int
) -> bool:
    """Delete from per-user table. Returns True if deleted."""
    table = _table_name(user_id)
    result = await db.execute(
        text(f"DELETE FROM {table} WHERE id = :id"),
        {"id": file_id},
    )
    return result.rowcount > 0 if result.rowcount else False


async def delete_file_as_admin(
    db: AsyncSession, file_id: str
) -> bool:
    """Delete file from any user's table (admin only)."""
    # Try to find which user owns this file
    from sqlalchemy import select
    from app.models.user import User
    users = await db.execute(select(User))
    for u in users.scalars().all():
        try:
            r = await db.execute(
                text(f"DELETE FROM {_table_name(u.id)} WHERE id = :id"),
                {"id": file_id},
            )
            if r.rowcount and r.rowcount > 0:
                return True
        except Exception:
            continue
    return False


async def clear_user_files(
    db: AsyncSession, user_id: int
) -> int:
    table = _table_name(user_id)
    result = await db.execute(text(f"DELETE FROM {table} WHERE 1=1"))
    return result.rowcount or 0


async def count_user_files(
    db: AsyncSession, user_id: int, start_date: str | None = None, end_date: str | None = None,
    file_type: str | None = None, search: str | None = None,
) -> int:
    table = _table_name(user_id)
    try:
        sql = f"SELECT COUNT(*) AS cnt FROM {table} WHERE 1=1"
        if start_date:
            sql += f" AND receivedAt >= '{start_date}T00:00:00'"
        if end_date:
            sql += f" AND receivedAt <= '{end_date}T23:59:59'"
        if file_type:
            types_list = [f"'{t.strip()}'" for t in file_type.split(",") if t.strip()]
            sql += f" AND type IN ({','.join(types_list)})"
        if search:
            sql += f" AND (name LIKE '%{search}%' OR description LIKE '%{search}%' OR tags LIKE '%{search}%')"
        result = await db.execute(text(sql))
        row = result.one()
        return row[0] if row else 0
    except Exception:
        return 0
