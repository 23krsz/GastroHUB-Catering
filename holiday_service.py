"""Mode libur global dari Supabase system_settings."""
from __future__ import annotations

import time

from supabase import Client

_HOLIDAY_KEY = "holiday_mode"
_DEFAULT = {
    "active": False,
    "message": (
        "Halo! Dapur GastroHUB libur hari ini. "
        "Silahkan hubungi admin untuk info lebih lanjut."
    ),
}

_cache: tuple[float, dict] | None = None
_CACHE_TTL_SEC = 60


def _fetch_settings(supabase: Client) -> dict:
    global _cache
    now = time.time()
    if _cache and now - _cache[0] < _CACHE_TTL_SEC:
        return _cache[1]

    row = (
        supabase.table("system_settings")
        .select("value_json")
        .eq("key", _HOLIDAY_KEY)
        .limit(1)
        .execute()
    )
    value = dict(_DEFAULT)
    if row.data:
        value.update(row.data[0].get("value_json") or {})

    _cache = (now, value)
    return value


def invalidate_cache():
    global _cache
    _cache = None


def is_holiday_mode(supabase: Client) -> tuple[bool, str]:
    settings = _fetch_settings(supabase)
    if settings.get("active"):
        return True, str(settings.get("message") or _DEFAULT["message"])
    return False, ""


def get_holiday_settings(supabase: Client) -> dict:
    return _fetch_settings(supabase)


def save_holiday_settings(supabase: Client, active: bool, message: str):
    from datetime import datetime, timezone

    payload = {"active": active, "message": message.strip()}
    supabase.table("system_settings").upsert({
        "key":         _HOLIDAY_KEY,
        "value_json":  payload,
        "updated_at":  datetime.now(timezone.utc).isoformat(),
    }).execute()
    invalidate_cache()
