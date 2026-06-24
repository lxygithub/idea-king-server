from pydantic import BaseModel, Field
from typing import Any


def _tags_to_str(v: Any) -> str | None:
    """Coerce tags from list to JSON string."""
    if v is None:
        return None
    if isinstance(v, list):
        import json
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, str):
        return v
    return str(v)


class FileResponse(BaseModel):
    id: str
    user_id: int
    name: str
    type: str
    localPath: str | None = None
    textContent: str | None = None
    sourceUri: str | None = None
    receivedAt: str | None = None
    mimeType: str | None = None
    fileSize: int | None = None
    s3Key: str | None = None
    uploadProgress: float | None = None
    uploadError: str | None = None
    uploadId: str | None = None
    uploadedParts: str | None = None
    tags: str | None = None
    description: str | None = None

    class Config:
        from_attributes = True


class SyncRequest(BaseModel):
    id: str | None = None
    name: str = Field(..., max_length=500)
    type: str = Field(..., max_length=50)
    localPath: str | None = None
    textContent: str | None = None
    sourceUri: str | None = None
    receivedAt: str | None = None
    mimeType: str | None = None
    fileSize: int | None = None
    s3Key: str | None = None
    uploadProgress: float | None = None
    uploadError: str | None = None
    uploadId: str | None = None
    uploadedParts: str | None = None
    description: str | None = None

    # Accept tags as string (JSON) or list, normalize to string
    tags: Any = None

    def model_post_init(self, __context):
        self.tags = _tags_to_str(self.tags)


class DeleteRequest(BaseModel):
    id: str


class SyncResponse(BaseModel):
    success: bool
    id: str
    message: str = "File synced"


class DeleteResponse(BaseModel):
    success: bool
    message: str = "File deleted"


class ClearResponse(BaseModel):
    success: bool
    deleted_count: int
    message: str = "All files cleared"
