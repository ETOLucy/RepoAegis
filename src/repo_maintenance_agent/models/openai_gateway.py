from __future__ import annotations

from decimal import Decimal
from typing import Any, TypeVar, cast

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.models.usage import UsageLedger, usage_from_response

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class OpenAIModelGateway:
    def __init__(
        self,
        *,
        client: Any,
        model: str,
        usage_ledger: UsageLedger | None = None,
        maximum_call_cost_cny: Decimal = Decimal("0"),
    ) -> None:
        self._client = client
        self._model = model
        self._usage_ledger = usage_ledger
        self._maximum_call_cost_cny = maximum_call_cost_cny

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAIModelGateway:
        if settings.openai_api_key is None:
            raise RuntimeError("OPENAI_API_KEY is required for live model execution")
        client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
        )
        return cls(client=client, model=settings.openai_model)

    async def structured(
        self,
        *,
        system: str,
        input_text: str,
        schema: type[SchemaT],
        max_attempts: int = 2,
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
                response = await self._client.responses.parse(
                    model=self._model,
                    instructions=system,
                    input=input_text,
                    text_format=schema,
                    store=False,
                )
                parsed = response.output_parsed
                if parsed is None:
                    raise RuntimeError("model did not return the requested structured output")
                if self._usage_ledger is not None and reservation is not None:
                    self._usage_ledger.record(
                        usage_from_response(response),
                        reservation_cny=reservation,
                    )
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
                    + str(error).splitlines()[0]
                    + "]"
                )
                continue
        assert last_error is not None
        raise last_error
