"""Core pydantic models shared across ingestion, indexing, retrieval, and eval.

These mirror the SQLite schema in ``db.py``; keeping them as plain pydantic
models (no ORM) means the mapping between Python and SQL stays explicit.
"""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

DocType = Literal["agenda", "minutes", "budget"]


class Document(BaseModel):
    """One source file (PDF) fetched from a city website."""

    id: str
    city: str
    url: str
    local_path: str | None = None
    title: str | None = None
    doc_type: DocType
    meeting_date: date | None = None
    fiscal_year: int | None = None
    sha256: str | None = None
    fetched_at: datetime | None = None


class Chunk(BaseModel):
    """A retrievable slice of a document page, with enough metadata to filter
    and to cite ``[city, title, page N]`` without a second lookup."""

    id: str
    doc_id: str
    city: str
    doc_type: DocType
    meeting_date: date | None = None
    fiscal_year: int | None = None
    page_number: int
    chunk_index: int
    text: str


class EvalCase(BaseModel):
    """One hand-labeled case from ``evals/golden_set.yaml``."""

    id: str
    question: str
    city: str | None = None
    relevant_doc_ids: list[str] = Field(default_factory=list)
    relevant_pages: list[int] = Field(default_factory=list)
    expected_answer_contains: list[str] = Field(default_factory=list)
    answerable: bool = True


class AgentStep(BaseModel):
    """One tool call in an agent run, persisted for tracing and cost tracking."""

    run_id: str
    step_index: int
    tool_name: str
    tool_input: dict[str, Any]
    tool_output_summary: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
