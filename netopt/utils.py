from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from typing import Any


CHINA_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(CHINA_TZ).replace(microsecond=0).isoformat()


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def weighted_score(*parts: tuple[float, float]) -> float:
    total_weight = sum(weight for _, weight in parts)
    if total_weight <= 0:
        return 0.0
    return round(sum(score * weight for score, weight in parts) / total_weight, 3)
