from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SWEbenchPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instance_id: str = Field(min_length=1, max_length=256)
    model_patch: str = Field(min_length=1)
    model_name_or_path: str = Field(min_length=1, max_length=256)

    @field_validator("model_patch")
    @classmethod
    def require_unified_diff(cls, value: str) -> str:
        if not (value.startswith("diff --git ") or value.startswith("--- ")):
            raise ValueError("model_patch must be a unified diff")
        return value


def write_predictions(path: Path, predictions: Sequence[SWEbenchPrediction]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    lines = (
        json.dumps(
            prediction.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for prediction in predictions
    )
    temporary.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    os.replace(temporary, path)
