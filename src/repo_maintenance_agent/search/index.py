from __future__ import annotations

import ast
import asyncio
import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from repo_maintenance_agent.domain.models import SearchHit, SearchQuery

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_IGNORED_PARTS = frozenset({".git", ".venv", ".worktrees", "node_modules", "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
_TEXT_SUFFIXES = frozenset(
    {".py", ".rs", ".go", ".java", ".js", ".jsx", ".ts", ".tsx", ".md", ".toml", ".yml", ".yaml"}
)
_MAX_FILE_BYTES = 2_000_000
_CHUNK_LINES = 80
_CHUNK_OVERLAP = 10


@dataclass(frozen=True, slots=True)
class CodeChunk:
    chunk_id: str
    tenant_id: str
    repo_id: str
    commit_sha: str
    path: str
    content: str
    line_start: int
    line_end: int
    symbol: str | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: tuple[tuple[float, ...], ...]
    input_tokens: int


class EmbeddingPort(Protocol):
    async def embed(self, texts: list[str]) -> EmbeddingBatch: ...


def ingest_workspace(
    workspace: Path, *, tenant_id: str, repo_id: str, commit_sha: str
) -> tuple[CodeChunk, ...]:
    root = workspace.resolve()
    chunks: list[CodeChunk] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if _IGNORED_PARTS.intersection(relative.parts) or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        lines = content.splitlines()
        relative_text = relative.as_posix()
        chunks.extend(
            _line_chunks(lines, tenant_id, repo_id, commit_sha, relative_text)
        )
        if path.suffix.lower() == ".py":
            chunks.extend(
                _python_symbol_chunks(
                    content, lines, tenant_id, repo_id, commit_sha, relative_text
                )
            )
    return tuple(chunks)


class BM25Search:
    def __init__(self, chunks: tuple[CodeChunk, ...], *, k1: float = 1.5, b: float = 0.75) -> None:
        self._chunks = chunks
        self._k1 = k1
        self._b = b

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        candidates = _scoped_chunks(self._chunks, query)
        if not candidates:
            return []
        tokenized = [_tokens(chunk.content) for chunk in candidates]
        terms = set(_tokens(query.text))
        if not terms:
            return []
        average_length = sum(len(tokens) for tokens in tokenized) / len(tokenized)
        document_frequency = {
            term: sum(term in tokens for tokens in tokenized) for term in terms
        }
        scored: list[tuple[float, CodeChunk]] = []
        for chunk, tokens in zip(candidates, tokenized, strict=True):
            frequencies = Counter(tokens)
            score = 0.0
            for term in terms:
                frequency = frequencies[term]
                if frequency == 0:
                    continue
                count = document_frequency[term]
                inverse_frequency = math.log(1 + (len(candidates) - count + 0.5) / (count + 0.5))
                normalization = frequency + self._k1 * (
                    1 - self._b + self._b * len(tokens) / max(average_length, 1)
                )
                score += inverse_frequency * frequency * (self._k1 + 1) / normalization
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].path, item[1].line_start))
        return [_hit(chunk, score, "bm25") for score, chunk in scored[: query.top_k]]


class SymbolSearch:
    def __init__(self, chunks: tuple[CodeChunk, ...]) -> None:
        self._chunks = chunks

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        query_tokens = set(_tokens(query.text)) - {
            "callers", "callee", "callees", "definition", "implements", "inherits",
            "reference", "references", "symbol",
        }
        scored: list[tuple[float, CodeChunk]] = []
        for chunk in _scoped_chunks(self._chunks, query):
            if chunk.symbol is None:
                continue
            symbol_tokens = set(_tokens(chunk.symbol))
            overlap = query_tokens & symbol_tokens
            if not overlap:
                continue
            leaf = chunk.symbol.rsplit(".", maxsplit=1)[-1].casefold()
            exact_bonus = 1.0 if leaf in query_tokens else 0.0
            scored.append((len(overlap) + exact_bonus, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].path, item[1].line_start))
        return [_hit(chunk, score, "symbol") for score, chunk in scored[: query.top_k]]


class VectorSearch:
    def __init__(
        self,
        chunks: tuple[CodeChunk, ...],
        embeddings: EmbeddingPort,
        *,
        batch_size: int = 64,
    ) -> None:
        if not 1 <= batch_size <= 256:
            raise ValueError("embedding batch size must be between 1 and 256")
        self._chunks = chunks
        self._embeddings = embeddings
        self._batch_size = batch_size
        self._vectors: dict[str, tuple[float, ...]] = {}
        self._lock = asyncio.Lock()

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        candidates = _scoped_chunks(self._chunks, query)
        if not candidates:
            return []
        await self._embed_missing(candidates)
        query_batch = await self._embeddings.embed([query.text])
        if len(query_batch.vectors) != 1:
            raise ValueError("embedding provider returned the wrong query vector count")
        query_vector = query_batch.vectors[0]
        scored = [
            (_cosine(query_vector, self._vectors[chunk.chunk_id]), chunk)
            for chunk in candidates
        ]
        scored = [item for item in scored if item[0] > 0]
        scored.sort(key=lambda item: (-item[0], item[1].path, item[1].line_start))
        return [_hit(chunk, score, "vector") for score, chunk in scored[: query.top_k]]

    async def _embed_missing(self, candidates: list[CodeChunk]) -> None:
        async with self._lock:
            missing = [chunk for chunk in candidates if chunk.chunk_id not in self._vectors]
            for offset in range(0, len(missing), self._batch_size):
                batch = missing[offset : offset + self._batch_size]
                response = await self._embeddings.embed([chunk.content for chunk in batch])
                if len(response.vectors) != len(batch):
                    raise ValueError("embedding provider returned the wrong vector count")
                for chunk, vector in zip(batch, response.vectors, strict=True):
                    if not vector:
                        raise ValueError("embedding provider returned an empty vector")
                    self._vectors[chunk.chunk_id] = vector


def _line_chunks(
    lines: list[str], tenant_id: str, repo_id: str, commit_sha: str, path: str
) -> list[CodeChunk]:
    chunks: list[CodeChunk] = []
    step = _CHUNK_LINES - _CHUNK_OVERLAP
    for offset in range(0, len(lines), step):
        selected = lines[offset : offset + _CHUNK_LINES]
        if not selected:
            continue
        chunks.append(
            _chunk(tenant_id, repo_id, commit_sha, path, selected, offset + 1, None)
        )
        if offset + _CHUNK_LINES >= len(lines):
            break
    return chunks


def _python_symbol_chunks(
    content: str,
    lines: list[str],
    tenant_id: str,
    repo_id: str,
    commit_sha: str,
    path: str,
) -> list[CodeChunk]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    chunks: list[CodeChunk] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunks.append(_symbol_chunk(node, node.name, lines, tenant_id, repo_id, commit_sha, path))
        elif isinstance(node, ast.ClassDef):
            chunks.append(_symbol_chunk(node, node.name, lines, tenant_id, repo_id, commit_sha, path))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    chunks.append(
                        _symbol_chunk(
                            child,
                            f"{node.name}.{child.name}",
                            lines,
                            tenant_id,
                            repo_id,
                            commit_sha,
                            path,
                        )
                    )
    return chunks


def _symbol_chunk(
    node: ast.AST,
    symbol: str,
    lines: list[str],
    tenant_id: str,
    repo_id: str,
    commit_sha: str,
    path: str,
) -> CodeChunk:
    start = int(getattr(node, "lineno"))
    end = int(getattr(node, "end_lineno", start))
    return _chunk(tenant_id, repo_id, commit_sha, path, lines[start - 1 : end], start, symbol)


def _chunk(
    tenant_id: str,
    repo_id: str,
    commit_sha: str,
    path: str,
    lines: list[str],
    line_start: int,
    symbol: str | None,
) -> CodeChunk:
    content = "\n".join(lines)
    identity = f"{commit_sha}:{path}:{line_start}:{symbol or ''}:{content}"
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return CodeChunk(
        chunk_id=f"{commit_sha[:12]}-{digest}",
        tenant_id=tenant_id,
        repo_id=repo_id,
        commit_sha=commit_sha,
        path=path,
        content=content,
        line_start=line_start,
        line_end=line_start + len(lines) - 1,
        symbol=symbol,
    )


def _scoped_chunks(chunks: tuple[CodeChunk, ...], query: SearchQuery) -> list[CodeChunk]:
    return [
        chunk
        for chunk in chunks
        if chunk.tenant_id == query.tenant_id
        and chunk.repo_id == query.repo_id
        and chunk.commit_sha == query.commit_sha
        and _path_allowed(chunk.path, query.allowed_paths)
    ]


def _path_allowed(path: str, allowed_paths: tuple[str, ...]) -> bool:
    return not allowed_paths or any(
        path == allowed.rstrip("/") or path.startswith(allowed.rstrip("/") + "/")
        for allowed in allowed_paths
    )


def _tokens(value: str) -> list[str]:
    return [token.casefold() for token in _TOKEN.findall(value)]


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("embedding dimensions do not match")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _hit(chunk: CodeChunk, score: float, source: str) -> SearchHit:
    return SearchHit(
        hit_id=chunk.chunk_id,
        path=chunk.path,
        content=chunk.content,
        score=score,
        source=source,
        symbol=chunk.symbol,
        line_start=chunk.line_start,
        line_end=chunk.line_end,
    )
