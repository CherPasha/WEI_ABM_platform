from pydantic import BaseModel
from typing import Optional


class CreateKeywordGroup(BaseModel):
    name: str


class RenameKeywordGroup(BaseModel):
    name: str


class CreateKeyword(BaseModel):
    keyword: str


class CreateStopWord(BaseModel):
    word: str


class UpdateProject(BaseModel):
    target_roles: list[str]


class SessionStatus(BaseModel):
    id: str
    filename: str
    status: str
    error_message: Optional[str] = None
    total_companies: int = 0
    names_done: int = 0
    postings_done: int = 0
    contacts_done: int = 0


class SessionListItem(BaseModel):
    id: str
    filename: str
    status: str
    total_companies: int = 0
    names_done: int = 0
    postings_done: int = 0
    contacts_done: int = 0
    created_at: Optional[str] = None


class ContactScanSettings(BaseModel):
    use_roles: bool
    keyword_only: bool
