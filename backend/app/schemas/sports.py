from datetime import datetime
import uuid

from pydantic import BaseModel, ConfigDict


class CompetitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    country: str | None
    code: str | None
    type: str
    active: bool


class FixtureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    provider: str
    provider_fixture_id: str
    kickoff_at: datetime
    status: str
    home_score: int | None
    away_score: int | None
