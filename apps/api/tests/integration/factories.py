"""Builders for synthetic test object graphs. No real documents or PII."""

import uuid

from app.db.models import (
    Chunk,
    Citation,
    Conversation,
    Document,
    DocumentVersion,
    Membership,
    MembershipRole,
    Message,
    MessageRole,
    User,
    Workspace,
)
from sqlalchemy.ext.asyncio import AsyncSession


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def make_user(session: AsyncSession, email: str | None = None) -> User:
    user = User(
        email=email or f"{unique('user')}@example.com",
        password_hash="x" * 60,
        full_name="Synthetic User",
    )
    session.add(user)
    await session.flush()
    return user


async def make_workspace(session: AsyncSession, owner: User) -> Workspace:
    workspace = Workspace(name="Test Workspace", slug=unique("ws"), created_by=owner.id)
    session.add(workspace)
    await session.flush()
    session.add(Membership(workspace_id=workspace.id, user_id=owner.id, role=MembershipRole.OWNER))
    await session.flush()
    return workspace


async def make_document(
    session: AsyncSession,
    workspace: Workspace,
    owner: User,
) -> Document:
    document = Document(
        workspace_id=workspace.id,
        created_by=owner.id,
        title="Synthetic Document",
        source_filename="synthetic.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
        sha256="a" * 64,
    )
    session.add(document)
    await session.flush()
    return document


async def make_version(session: AsyncSession, document: Document) -> DocumentVersion:
    version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        storage_key=f"documents/{unique('key')}.pdf",
        sha256="b" * 64,
        size_bytes=1024,
        page_count=1,
    )
    session.add(version)
    await session.flush()
    return version


async def make_chunk(
    session: AsyncSession,
    workspace: Workspace,
    version: DocumentVersion,
    *,
    chunk_index: int = 0,
    char_start: int = 0,
    char_end: int = 42,
) -> Chunk:
    chunk = Chunk(
        workspace_id=workspace.id,
        document_version_id=version.id,
        chunk_index=chunk_index,
        content="Synthetic evidence text.",
        content_hash="c" * 64,
        page_number=1,
        char_start=char_start,
        char_end=char_end,
        language="ta",
    )
    session.add(chunk)
    await session.flush()
    return chunk


async def make_conversation_with_answer(
    session: AsyncSession,
    workspace: Workspace,
    owner: User,
) -> Message:
    conversation = Conversation(workspace_id=workspace.id, created_by=owner.id)
    session.add(conversation)
    await session.flush()
    message = Message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="A synthetic grounded answer.",
    )
    session.add(message)
    await session.flush()
    return message


async def make_citation(session: AsyncSession, message: Message, chunk: Chunk) -> Citation:
    citation = Citation(
        message_id=message.id,
        chunk_id=chunk.id,
        claim_text="A synthetic claim.",
        claim_start=0,
        claim_end=18,
        quote_text="Synthetic evidence",
        quote_start=0,
        quote_end=18,
        page_number=1,
    )
    session.add(citation)
    await session.flush()
    return citation
