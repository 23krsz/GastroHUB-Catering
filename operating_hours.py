"""Jam operasional dapur & batch pengiriman (WITA)."""
from __future__ import annotations

import datetime
import html
from zoneinfo import ZoneInfo

APP_TZ = ZoneInfo("Asia/Makassar")

ORDER_OPEN  = datetime.time(6, 0)
ORDER_CLOSE = datetime.time(18, 0)
BATCH_09    = datetime.time(9, 0)
BATCH_12    = datetime.time(12, 0)
BATCH_15    = datetime.time(15, 0)


def now_wita() -> datetime.datetime:
    return datetime.datetime.now(APP_TZ)


def get_delivery_date(now: datetime.datetime | None = None) -> datetime.date:
    if now is None:
        now = now_wita()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=APP_TZ)
    else:
        now = now.astimezone(APP_TZ)
    return now.date()


def is_order_window_open(
    now: datetime.datetime | None = None,
) -> tuple[bool, str | None]:
    """Return (open, reason). reason: 'before_open' | 'after_close' | None."""
    if now is None:
        now = now_wita()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=APP_TZ)
    else:
        now = now.astimezone(APP_TZ)

    t = now.time()
    if t < ORDER_OPEN:
        return False, "before_open"
    if t > ORDER_CLOSE:
        return False, "after_close"
    return True, None


def resolve_delivery(now: datetime.datetime | None = None) -> str:
    """Return delivery slot: '09:00' | '12:00' | '15:00' | '18:00'."""
    if now is None:
        now = now_wita()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=APP_TZ)
    else:
        now = now.astimezone(APP_TZ)

    t = now.time()
    if t <= BATCH_09:
        return "09:00"
    if t <= BATCH_12:
        return "12:00"
    if t <= BATCH_15:
        return "15:00"
    return "18:00"


def closed_message(member_nama: str, reason: str) -> str:
    nama = html.escape(member_nama)
    if reason == "before_open":
        return (
            f"Halo <b>{nama}</b>, hari ini dapur belum buka, "
            f"silahkan tunggu sebentar lagi ya!"
        )
    return (
        f"Halo <b>{nama}</b>, hari ini dapur sudah tutup, "
        f"silahkan kembali lagi besok ya!"
    )


def format_operating_info() -> str:
    return (
        "🕕 <b>Jam order</b>     : 06:00 – 18:00 WITA\n"
        "🚚 <b>Pengiriman</b>    : 09:00, 12:00, 15:00 & 18:00 WITA\n\n"
        "<b>Aturan pengiriman:</b>\n"
        "• Order 06:00–09:00 → menu dikirim jam <b>09:00</b> WITA\n"
        "• Order 09:01–12:00 → menu dikirim jam <b>12:00</b> WITA\n"
        "• Order 12:01–15:00 → menu dikirim jam <b>15:00</b> WITA\n"
        "• Order 15:01–18:00 → menu dikirim jam <b>18:00</b> WITA\n\n"
        "<i>Di luar jam 06:00–18:00 WITA, order tidak dapat diproses.</i>"
    )
