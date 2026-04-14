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
    stubCount: int = 0
    recentNotes: list[str] = []
    createdAt: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_row(
        cls,
        row,
        recent_notes: list[str] | None = None,
        stub_count: int = 0,
    ) -> "DocumentResponse":
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
            stubCount=stub_count,
            recentNotes=recent_notes or [],
            createdAt=row.created_at,
        )
