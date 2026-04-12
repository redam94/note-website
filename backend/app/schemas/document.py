from __future__ import annotations

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: int
    filename: str
    originalName: str
    mimeType: str
    contentRaw: str | None
    status: str
    error: str | None
    processingStep: str | None
    notesCount: int | None
    createdAt: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_row(cls, row) -> "DocumentResponse":
        return cls(
            id=row.id,
            filename=row.filename,
            originalName=row.original_name,
            mimeType=row.mime_type,
            contentRaw=row.content_raw,
            status=row.status,
            error=row.error,
            processingStep=getattr(row, "processing_step", None),
            notesCount=getattr(row, "notes_count", None),
            createdAt=row.created_at,
        )
