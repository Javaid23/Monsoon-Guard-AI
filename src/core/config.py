"""Single entry point for project configuration and paths.

Everything that reads configs/project.yaml goes through here so that no script
hard-codes a threshold, a bounding box or a year range.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "configs" / "project.yaml"


class Config(dict):
    """Dict with dotted access, e.g. cfg.get_path('raw_imerg')."""

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AttributeError(item) from exc

    def get_path(self, key: str) -> Path:
        """Resolve a key under `paths:` to an absolute path, creating it."""
        rel = self["paths"][key]
        path = REPO_ROOT / rel
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache(maxsize=1)
def load_config(path: Path | str | None = None) -> Config:
    path = Path(path) if path else CONFIG_PATH
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    cfg = Config(raw)
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    """Fail loudly on a config that would silently produce wrong science."""
    region = cfg["region"]
    assert region["north"] > region["south"], "region north must exceed south"
    assert region["east"] > region["west"], "region east must exceed west"

    crop = region["crop"]
    assert crop["north"] >= region["north"], "crop must cover the region (north)"
    assert crop["south"] <= region["south"], "crop must cover the region (south)"
    assert crop["east"] >= region["east"], "crop must cover the region (east)"
    assert crop["west"] <= region["west"], "crop must cover the region (west)"

    res = region["grid_resolution_deg"]
    n_lat = int(round((region["north"] - region["south"]) / res)) + 1
    n_lon = int(round((region["east"] - region["west"]) / res)) + 1
    assert [n_lat, n_lon] == list(region["expected_shape"]), (
        f"grid shape {[n_lat, n_lon]} does not match expected "
        f"{region['expected_shape']}"
    )

    # Splits must not overlap (CLAUDE.md section 8.1).
    split = cfg["split"]
    train = set(split["train_years"])
    cal = set(split["calibration_years"])
    test = set(split["test_years"])
    assert not (train & cal), "train and calibration years overlap"
    assert not (train & test), "train and test years overlap"
    assert not (cal & test), "calibration and test years overlap"

    bands = cfg["trust_bands"]
    assert 0 < bands["high_max"] < bands["medium_max"] < 1, "trust bands misordered"


# --- Bounding boxes in the orderings different APIs expect ------------------

def crop_bbox_wsen(cfg: Config) -> tuple[float, float, float, float]:
    """(west, south, east, north) - earthaccess / CMR ordering."""
    c = cfg["region"]["crop"]
    return (c["west"], c["south"], c["east"], c["north"])


def region_area_nwse(cfg: Config) -> str:
    """'N/W/S/E' string - ECMWF MARS ordering."""
    r = cfg["region"]
    return f"{r['north']}/{r['west']}/{r['south']}/{r['east']}"


# --- Season calendar --------------------------------------------------------

@dataclass(frozen=True)
class SeasonMonth:
    year: int
    month: int

    @property
    def start(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def end(self) -> date:
        if self.month == 12:
            return date(self.year, 12, 31)
        return date(self.year, self.month + 1, 1) - timedelta(days=1)

    @property
    def n_days(self) -> int:
        return (self.end - self.start).days + 1

    def __str__(self) -> str:
        return f"{self.year}-{self.month:02d}"


def season_months(cfg: Config, years: list[int] | None = None) -> Iterator[SeasonMonth]:
    """Yield every (year, month) in the configured JJAS seasons, in order."""
    years = years if years is not None else cfg["season"]["years"]
    for year in years:
        for month in cfg["season"]["months"]:
            yield SeasonMonth(year, month)


def expected_season_days(cfg: Config, years: list[int] | None = None) -> int:
    return sum(m.n_days for m in season_months(cfg, years))


# --- Credentials ------------------------------------------------------------

def load_dotenv_if_present() -> None:
    """Load .env into the environment. No-op if the file or lib is missing."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        # Minimal fallback parser so the scripts still work without python-dotenv.
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
        return
    load_dotenv(env_path)


# Values left as-is from .env.example are not credentials.
_PLACEHOLDERS = {
    "your_username", "your_password", "your_api_key", "your_email",
    "changeme", "xxx", "none", "todo",
}


def get_credential(name: str) -> str | None:
    """Return an environment value, or None if it is blank or a placeholder.

    A half-filled .env is a common failure and produces a confusing login
    error several steps later, so it is caught here instead.
    """
    value = (os.getenv(name) or "").strip().strip('"').strip("'")
    if not value or value.lower() in _PLACEHOLDERS:
        return None
    return value


if __name__ == "__main__":
    cfg = load_config()
    print(f"config:   {CONFIG_PATH}")
    print(f"region:   {cfg['region']['id']}")
    print(f"crop box: {crop_bbox_wsen(cfg)}  (west, south, east, north)")
    print(f"mars area:{region_area_nwse(cfg)}  (N/W/S/E)")
    print(f"grid:     {cfg['region']['expected_shape']} at {cfg['region']['grid_resolution_deg']} deg")
    months = list(season_months(cfg))
    print(f"months:   {len(months)} ({months[0]} .. {months[-1]})")
    print(f"days:     {expected_season_days(cfg)} JJAS days total")
    for name in ("train", "calibration", "test"):
        print(f"{name:12s}: {cfg['split'][name + '_years']}")
