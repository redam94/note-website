from pydantic import BaseModel


class AskRequest(BaseModel):
    question: str


class LinkRequest(BaseModel):
    noteId: int
