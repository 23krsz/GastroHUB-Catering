"""Fetch daily menu templates from Supabase."""
from __future__ import annotations

import datetime
import time
from zoneinfo import ZoneInfo

from supabase import Client

APP_TZ = ZoneInfo("Asia/Makassar")
DAY_KEYS = (
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
)

_cache: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL_SEC = 300


def get_day_key(now: datetime.datetime | None = None) -> str:
    if now is None:
        now = datetime.datetime.now(APP_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=APP_TZ)
    else:
        now = now.astimezone(APP_TZ)
    return now.strftime("%A").lower()


def get_today_date(now: datetime.datetime | None = None) -> datetime.date:
    if now is None:
        now = datetime.datetime.now(APP_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=APP_TZ)
    else:
        now = now.astimezone(APP_TZ)
    return now.date()


def _to_bot_menu(template: dict, items: list[dict], *, is_sold_out: bool = False) -> dict:
    detail = []
    for it in sorted(items, key=lambda x: (x.get("item_order") or 0, x.get("id") or 0)):
        detail.append({
            "nama_item":     it["nama_item"],
            "porsi_gram":    float(it["porsi_gram"] or 0),
            "kalori":        float(it["kalori"] or 0),
            "protein_gram":  float(it["protein_gram"] or 0),
            "karbo_gram":    float(it["karbo_gram"] or 0),
            "lemak_gram":    float(it["lemak_gram"] or 0),
        })
    return {
        "id":              template["id"],
        "slot":            template["slot"],
        "nama":            template["nama"],
        "porsi_std":       float(template.get("porsi_std") or 0),
        "kalori_std":      float(template["kalori_std"]),
        "harga_std":       float(template["harga_std"]),
        "detail_item_std": detail,
        "is_sold_out":     is_sold_out,
    }


def get_menus_for_day(supabase: Client, day_key: str | None = None) -> list[dict]:
    """Return 3 menu templates for day_key in bot-compatible format."""
    day_key = (day_key or get_day_key()).lower()
    if day_key not in DAY_KEYS:
        raise ValueError(f"day_key tidak valid: {day_key}")

    now = time.time()
    cached = _cache.get(day_key)
    if cached and now - cached[0] < _CACHE_TTL_SEC:
        return cached[1]

    day_row = (
        supabase.table("menu_days")
        .select("id, day_key, is_active")
        .eq("day_key", day_key)
        .limit(1)
        .execute()
    )
    if not day_row.data or not day_row.data[0].get("is_active", True):
        raise LookupError(f"Menu hari '{day_key}' tidak ditemukan atau nonaktif.")

    day_id = day_row.data[0]["id"]
    templates = (
        supabase.table("menu_templates")
        .select("*")
        .eq("day_id", day_id)
        .eq("is_active", True)
        .order("slot")
        .execute()
    ).data or []

    if len(templates) < 3:
        raise LookupError(
            f"Menu {day_key} belum lengkap ({len(templates)}/3). Hubungi admin."
        )

    template_ids = [t["id"] for t in templates]
    items = (
        supabase.table("menu_template_items")
        .select("*")
        .in_("template_id", template_ids)
        .order("item_order")
        .execute()
    ).data or []

    by_template: dict[int, list] = {}
    for it in items:
        by_template.setdefault(it["template_id"], []).append(it)

    today = get_today_date().isoformat()
    availability = (
        supabase.table("menu_availability")
        .select("template_id, is_sold_out")
        .eq("available_date", today)
        .in_("template_id", template_ids)
        .execute()
    ).data or []
    sold_out_ids = {
        row["template_id"]
        for row in availability
        if row.get("is_sold_out")
    }

    menus = [
        _to_bot_menu(
            t,
            by_template.get(t["id"], []),
            is_sold_out=t["id"] in sold_out_ids,
        )
        for t in templates
    ]
    _cache[day_key] = (now, menus)
    return menus


def set_sold_out(
    supabase: Client,
    template_id: int,
    available_date: datetime.date | str,
    sold_out: bool,
):
    if isinstance(available_date, datetime.date):
        available_date = available_date.isoformat()

    supabase.table("menu_availability").upsert({
        "template_id":    template_id,
        "available_date": available_date,
        "is_sold_out":    sold_out,
        "updated_at":     datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, on_conflict="template_id,available_date").execute()
    invalidate_cache()


def invalidate_cache(day_key: str | None = None):
    if day_key:
        _cache.pop(day_key.lower(), None)
    else:
        _cache.clear()
