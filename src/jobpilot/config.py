from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path

from .paths import jobpilot_home


@dataclass(frozen=True)
class CanaryConfig:
    search_term: str
    location: str | None
    results_wanted: int
    min_results: int


@dataclass(frozen=True)
class SiteConfig:
    name: str
    enabled: bool
    country_indeed: str
    hours_old: int
    results_wanted: int
    fetch_descriptions: bool
    canary: CanaryConfig
    site: str = ""

    def __post_init__(self) -> None:
        if not self.site:
            object.__setattr__(self, "site", self.name)


@dataclass(frozen=True)
class ATSConfig:
    name: str
    vendor: str
    company_name: str
    endpoint: str
    enabled: bool
    canary_min_results: int = 1
    fetch_timeout_seconds: float = 20.0


@dataclass(frozen=True)
class StructuredConfig:
    name: str
    company_name: str
    endpoint: str
    enabled: bool
    canary_min_results: int = 1
    fetch_timeout_seconds: float = 20.0


@dataclass(frozen=True)
class WigoloConfig:
    name: str
    company_name: str
    endpoint: str
    command: tuple[str, ...]
    canary_term: str
    canary_location: str | None
    enabled: bool
    canary_min_results: int = 1
    fetch_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class Config:
    home: Path
    data_dir: Path
    out_dir: Path
    score_batch: int
    min_request_gap_seconds: float
    sites: dict[str, SiteConfig]
    ats_sources: dict[str, ATSConfig] = field(default_factory=dict)
    structured_sources: dict[str, StructuredConfig] = field(default_factory=dict)
    wigolo_sources: dict[str, WigoloConfig] = field(default_factory=dict)
    # Model for batch job scoring. The Telegram bot shares the same Claude
    # subscription limit; "haiku" is the cheaper option.
    scoring_model: str = "claude-sonnet-5"
    scoring_effort: str = "low"
    llm: "LLMConfig" = field(default_factory=lambda: LLMConfig())


@dataclass(frozen=True)
class LLMConfig:
    """Which brain answers Telegram and scores jobs.

    "claude" runs the local ``claude`` CLI (subscription login on this
    machine). "bedrock" calls Amazon Bedrock with the machine's AWS
    credentials, so the bot can run on a server. JOBPILOT_LLM_PROVIDER in the
    environment overrides the file, so one checkout can serve both.
    """
    provider: str = "claude"
    region: str = "ap-south-1"
    bot_model: str = "moonshotai.kimi-k2.5"
    fallback_model: str = "mistral.mistral-large-3-675b-instruct"
    scoring_model: str = "qwen.qwen3-235b-a22b-2507-v1:0"


def _llm_config(block: dict) -> LLMConfig:
    import os
    provider = str(os.environ.get("JOBPILOT_LLM_PROVIDER") or block.get("provider") or "claude")
    provider = provider.strip().lower()
    if provider not in {"claude", "bedrock"}:
        raise ValueError(f"[llm] provider must be claude or bedrock, not {provider!r}")
    d = LLMConfig()
    return LLMConfig(
        provider=provider,
        region=str(block.get("region") or d.region),
        bot_model=str(block.get("bot_model") or d.bot_model),
        fallback_model=str(block.get("fallback_model") or d.fallback_model),
        scoring_model=str(block.get("scoring_model") or d.scoring_model),
    )


def load_config(path: Path | None = None) -> Config:
    home = jobpilot_home()
    cfg_path = Path(path) if path is not None else home / "config.toml"
    raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))

    sites: dict[str, SiteConfig] = {}
    for name, block in raw.get("sites", {}).items():
        if "canary" not in block:
            raise ValueError(
                f"site '{name}' has no canary; a site without a canary cannot "
                "be distinguished from a blocked one"
            )
        c = block["canary"]
        if "hours_old" in c:
            raise ValueError(
                f"site '{name}' canary declares hours_old; a canary must carry no "
                "recency filter or a quiet week becomes a false alarm"
            )
        if int(c.get("min_results", 0)) < 1:
            raise ValueError(
                f"site '{name}' canary min_results must be >= 1; a canary that "
                "only asserts '> 0' cannot detect a partially crippled source"
            )
        sites[name] = SiteConfig(
            name=name,
            enabled=block["enabled"],
            country_indeed=block["country_indeed"],
            hours_old=block["hours_old"],
            results_wanted=block["results_wanted"],
            fetch_descriptions=block.get("fetch_descriptions", True),
            canary=CanaryConfig(
                search_term=c["search_term"],
                location=c.get("location"),
                results_wanted=int(c.get("results_wanted", 20)),
                min_results=int(c["min_results"]),
            ),
            site=block.get("site", name),
        )

    ats_sources: dict[str, ATSConfig] = {}
    for name, block in raw.get("ats_sources", {}).items():
        vendor = str(block.get("vendor", "")).strip().lower()
        if vendor not in {"greenhouse", "lever", "workday", "successfactors", "remoteok",
                          "remotive", "workday_cxs", "oracle_hcm", "bamboohr", "breezy", "ashby", "smartrecruiters", "foundit", "himalayas", "jobicy"}:
            raise ValueError(
                f"ATS source '{name}' has unsupported vendor {vendor!r}"
            )
        endpoint = str(block.get("endpoint", "")).strip()
        if not endpoint.startswith(("https://", "http://")):
            raise ValueError(f"ATS source '{name}' endpoint must be an HTTP(S) URL")
        company_name = str(block.get("company_name", name)).strip()
        if not company_name:
            raise ValueError(f"ATS source '{name}' company_name must not be empty")
        minimum = int(block.get("canary_min_results", 1))
        if minimum < 1:
            raise ValueError(f"ATS source '{name}' canary_min_results must be >= 1")
        timeout = float(block.get("fetch_timeout_seconds", 20.0))
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError(f"ATS source '{name}' fetch_timeout_seconds must be > 0")
        ats_sources[name] = ATSConfig(
            name=name,
            vendor=vendor,
            company_name=company_name,
            endpoint=endpoint,
            enabled=bool(block.get("enabled", False)),
            canary_min_results=minimum,
            fetch_timeout_seconds=timeout,
        )

    structured_sources: dict[str, StructuredConfig] = {}
    for name, block in raw.get("structured_sources", {}).items():
        endpoint = str(block.get("endpoint", "")).strip()
        if not endpoint.startswith(("https://", "http://")):
            raise ValueError(
                f"structured source '{name}' endpoint must be an HTTP(S) URL"
            )
        company_name = str(block.get("company_name", name)).strip()
        if not company_name:
            raise ValueError(f"structured source '{name}' company_name must not be empty")
        minimum = int(block.get("canary_min_results", 1))
        if minimum < 1:
            raise ValueError(
                f"structured source '{name}' canary_min_results must be >= 1"
            )
        timeout = float(block.get("fetch_timeout_seconds", 20.0))
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError(
                f"structured source '{name}' fetch_timeout_seconds must be > 0"
            )
        structured_sources[name] = StructuredConfig(
            name=name,
            company_name=company_name,
            endpoint=endpoint,
            enabled=bool(block.get("enabled", False)),
            canary_min_results=minimum,
            fetch_timeout_seconds=timeout,
        )

    wigolo_sources: dict[str, WigoloConfig] = {}
    for name, block in raw.get("wigolo_sources", {}).items():
        endpoint = str(block.get("endpoint", "")).strip()
        if not endpoint.startswith(("https://", "http://")):
            raise ValueError(f"Wigolo source '{name}' endpoint must be an HTTP(S) URL")
        company_name = str(block.get("company_name", name)).strip()
        command_raw = block.get("command", [])
        if not isinstance(command_raw, list) or not command_raw or not all(command_raw):
            raise ValueError(
                f"Wigolo source '{name}' command must be a non-empty argv list"
            )
        canary_term = str(block.get("canary_term", "")).strip()
        if not canary_term:
            raise ValueError(f"Wigolo source '{name}' canary_term must not be empty")
        minimum = int(block.get("canary_min_results", 1))
        if minimum < 1:
            raise ValueError(f"Wigolo source '{name}' canary_min_results must be >= 1")
        timeout = float(block.get("fetch_timeout_seconds", 30.0))
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError(f"Wigolo source '{name}' fetch_timeout_seconds must be > 0")
        wigolo_sources[name] = WigoloConfig(
            name=name,
            company_name=company_name,
            endpoint=endpoint,
            command=tuple(str(item) for item in command_raw),
            canary_term=canary_term,
            canary_location=block.get("canary_location"),
            enabled=bool(block.get("enabled", False)),
            canary_min_results=minimum,
            fetch_timeout_seconds=timeout,
        )

    base = home
    return Config(
        home=base,
        data_dir=base / raw["paths"]["data_dir"],
        out_dir=base / raw["paths"]["out_dir"],
        score_batch=raw["limits"]["score_batch"],
        min_request_gap_seconds=float(raw["limits"]["min_request_gap_seconds"]),
        scoring_model=str(raw.get("scoring", {}).get("model") or "claude-sonnet-5"),
        scoring_effort=str(raw.get("scoring", {}).get("effort") or "low"),
        sites=sites,
        ats_sources=ats_sources,
        structured_sources=structured_sources,
        wigolo_sources=wigolo_sources,
        llm=_llm_config(raw.get("llm", {})),
    )
