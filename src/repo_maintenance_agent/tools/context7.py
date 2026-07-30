from __future__ import annotations

import re
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class McpInvoker(Protocol):
    async def call(self, tool: str, arguments: dict[str, str]) -> dict[str, object]: ...


class DocumentationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    library_id: str
    content: str


class Context7Adapter:
    def __init__(self, invoker: McpInvoker) -> None:
        self._invoker = invoker

    async def query(self, library_name: str, question: str) -> DocumentationResult:
        if not library_name.strip() or not question.strip():
            raise ValueError("library name and question are required")
        resolved = await self._invoker.call(
            "resolve-library-id",
            {"libraryName": library_name, "query": question},
        )
        library_id = resolved.get("library_id")
        if not isinstance(library_id, str) or not re.fullmatch(
            r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?",
            library_id,
        ):
            raise RuntimeError("Context7 did not return a valid library ID")
        queried = await self._invoker.call(
            "query-docs",
            {"libraryId": library_id, "query": question},
        )
        content = queried.get("content")
        if not isinstance(content, str):
            raise RuntimeError("Context7 did not return documentation content")
        return DocumentationResult(library_id=library_id, content=content)
