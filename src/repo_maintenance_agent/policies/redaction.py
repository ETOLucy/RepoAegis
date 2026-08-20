from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

_SENSITIVE_KEYS = re.compile(
    r"(^|_)(authorization|api_?key|password|passwd|secret|access_?token|"
    r"refresh_?token|private_?key|cookie)($|_)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)(authorization\s*:\s*bearer\s+|bearer\s+)[^\s'\"]+")
_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


class Redactor:
    replacement = "[REDACTED]"

    def redact(self, value: Any, *, key: str | None = None) -> Any:
        if key is not None and _SENSITIVE_KEYS.search(key) and key.lower() != "token_count":
            return self.replacement
        if isinstance(value, Mapping):
            return {str(k): self.redact(v, key=str(k)) for k, v in deepcopy(value).items()}
        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            redacted = _BEARER.sub(lambda match: f"{match.group(1)}{self.replacement}", value)
            return _OPENAI_KEY.sub(self.replacement, redacted)
        return value
