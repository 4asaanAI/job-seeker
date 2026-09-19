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
class Config:
    home: Path
    data_dir: Path
    out_dir: Path
    score_batch: int
    min_request_gap_seconds: float
    sites: dict[str, SiteConfig]
    ats_sources: dict[str, ATSConfig] = field(default_factory=dict)


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
        if vendor not in {"greenhouse", "lever", "workday", "successfactors"}:
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

    base = home
    return Config(
        home=base,
        data_dir=base / raw["paths"]["data_dir"],
        out_dir=base / raw["paths"]["out_dir"],
        score_batch=raw["limits"]["score_batch"],
        min_request_gap_seconds=float(raw["limits"]["min_request_gap_seconds"]),
        sites=sites,
        ats_sources=ats_sources,
    )
