from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Literal, TypeVar, cast

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.models.usage import UsageLedger, usage_from_response

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_MODEL_TIMEOUT_SECONDS = 180
_MAX_OUTPUT_TOKENS = 16_384


class OpenAIModelGateway:
    def __init__(
        self,
        *,
        client: Any,
        model: str,
        api_style: Literal["responses", "chat-json"] = "responses",
        usage_ledger: UsageLedger | None = None,
        maximum_call_cost_cny: Decimal = Decimal("0"),
    ) -> None:
        self._client = client
        self._model = model
        self._api_style = api_style
        self._usage_ledger = usage_ledger
        self._maximum_call_cost_cny = maximum_call_cost_cny

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        usage_ledger: UsageLedger | None = None,
        maximum_call_cost_cny: Decimal = Decimal("0"),
    ) -> OpenAIModelGateway:
        if settings.openai_api_key is None:
            raise RuntimeError("OPENAI_API_KEY is required for live model execution")
        client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
            timeout=_MODEL_TIMEOUT_SECONDS,
            max_retries=0,
        )
        return cls(
            client=client,
            model=settings.openai_model,
            api_style=settings.model_api_style,
            usage_ledger=usage_ledger,
            maximum_call_cost_cny=maximum_call_cost_cny,
        )

    async def structured(
        self,
        *,
        system: str,
        input_text: str,
        schema: type[SchemaT],
        max_attempts: int = 3,
    ) -> SchemaT:
        if max_attempts < 1 or max_attempts > 5:
            raise ValueError("structured attempts must be between 1 and 5")
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            reservation = (
                self._usage_ledger.reserve(self._maximum_call_cost_cny)
                if self._usage_ledger is not None
                else None
            )
            try:
                if self._api_style == "chat-json":
                    response = await self._client.chat.completions.create(
                        model=self._model,
                        messages=[
                            {
                                "role": "system",
                                "content": _json_system_prompt(system, schema),
                            },
                            {"role": "user", "content": input_text},
                        ],
                        response_format={"type": "json_object"},
                        max_tokens=_MAX_OUTPUT_TOKENS,
                        extra_body={"thinking": {"type": "disabled"}},
                    )
                    _record_usage(self._usage_ledger, reservation, response)
                    content = response.choices[0].message.content
                    if not isinstance(content, str) or not content.strip():
                        raise RuntimeError(
                            "model did not return the requested structured output"
                        )
                    return schema.model_validate_json(content)

                response = await self._client.responses.parse(
                    model=self._model,
                    instructions=system,
                    input=input_text,
                    text_format=schema,
                    store=False,
                    max_output_tokens=_MAX_OUTPUT_TOKENS,
                )
                _record_usage(self._usage_ledger, reservation, response)
                parsed = response.output_parsed
                if parsed is None:
                    raise RuntimeError("model did not return the requested structured output")
                return cast(SchemaT, parsed)
            except ValidationError as error:
                last_error = error
                if attempt == max_attempts - 1:
                    break
                input_text = (
                    input_text
                    + "\n\n[Your previous response was invalid and will not be used. "
                    + "Correct it and return valid JSON matching the required schema. "
                    + "Validation error: "
                    + _validation_feedback(error)
                    + "]"
                )
                continue
        assert last_error is not None
        raise last_error


def _validation_feedback(error: ValidationError) -> str:
    issues = [
        {"loc": list(issue["loc"]), "type": issue["type"], "msg": issue["msg"]}
        for issue in error.errors(include_url=False, include_input=False)[:10]
    ]
    return json.dumps(issues, ensure_ascii=False, separators=(",", ":"))


def _json_system_prompt(system: str, schema: type[BaseModel]) -> str:
    json_schema = json.dumps(
        schema.model_json_schema(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return (
        f"{system}\n\nReturn only one JSON object matching this JSON Schema. "
        f"Do not wrap it in Markdown. JSON Schema: {json_schema}"
    )


def _record_usage(
    ledger: UsageLedger | None,
    reservation: Decimal | None,
    response: Any,
) -> None:
    if ledger is not None and reservation is not None:
        ledger.record(usage_from_response(response), reservation_cny=reservation)
