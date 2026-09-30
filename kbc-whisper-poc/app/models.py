"""Request bodies, validated by FastAPI/pydantic before any code runs."""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TransactionIn(Strict):
    direction: Literal["in", "out"]
    counterparty: str = Field(min_length=1, max_length=120)
    amount_cents: int = Field(gt=0, le=100_000_000, description="Positive amount in euro cents")
    mcc: Optional[str] = Field(default=None, pattern=r"^\d{4}$", description="Card merchant category code")
    message: str = Field(default="", max_length=140)
    channel: Literal["card", "transfer"] = "transfer"

    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {
        "direction": "out", "counterparty": "EASYJET AIRLINE CO", "amount_cents": 21999,
        "mcc": "4511", "message": "Online card payment", "channel": "card"}})


class ConsentIn(Strict):
    connected: bool


class FeedbackIn(Strict):
    action: Literal["opened", "dismissed", "muted"]


class QuoteIn(Strict):
    answers: Dict[str, str]


class AcceptIn(Strict):
    item_ids: List[str] = Field(min_length=1, max_length=10)
