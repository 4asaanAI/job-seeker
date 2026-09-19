# src/jobpilot/store/models.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Seeker:
    id: int
    name: str
    telegram_user_id: int | None
    profile_path: str
    output_token: str
    active: bool
    daily_cap: int


@dataclass(frozen=True)
class Posting:
    company_name: str
    source: str
    external_id: str
    title: str
    location: str | None
    description: str | None
    url: str | None
    posted_at: str | None


@dataclass(frozen=True)
class SeekerJob:
    seeker_id: int
    posting_id: int
    status: str
    title: str
    company_name: str
    url: str | None
    fit_score: float | None = None
    reasoning: str | None = None
