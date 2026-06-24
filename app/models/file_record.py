from datetime import datetime

from sqlalchemy import (
    Integer, String, BigInteger, DateTime, Float, Text, ForeignKey, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FileRecord(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )

    # Matches the existing Flutter SharedFile model fields
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    localPath: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    textContent: Mapped[str | None] = mapped_column(Text, nullable=True)
    sourceUri: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    receivedAt: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mimeType: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fileSize: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    s3Key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    uploadProgress: Mapped[float | None] = mapped_column(Float, nullable=True)
    uploadError: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploadId: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploadedParts: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    owner = relationship("User", back_populates="files")
