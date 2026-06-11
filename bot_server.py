"""
bot_server.py — Bot 24/7 untuk semua member Sassyroll Catering.
Jalankan sekali, biarkan berjalan terus di background.
"""
import os
import time
import requests
import datetime
import html
import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton,
)
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv(override=True)

def required_env(name: str) -> str:
    v = os.getenv(name)
    if not v or not v.strip():
        raise RuntimeError(f"Variabel .env '{name}' belum diisi.")
    return v.strip()

TELEGRAM_BOT_TOKEN   = required_env("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID        = required_env("ADMIN_CHAT_ID")
INTERNAL_API_KEY     = required_env("INTERNAL_API_KEY")
STRAVA_CLIENT_ID     = required_env("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = required_env("STRAVA_CLIENT_SECRET")
BASE_URL             = os.getenv("BASE_URL", "http://localhost:8000")
SUPABASE_URL         = required_env("SUPABASE_URL")
SUPABASE_SERVICE_KEY = required_env("SUPABASE_SERVICE_KEY")
UX_MODE              = os.getenv("UX_MODE", "B").upper()  # "B" atau "C"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

from menu_service import get_day_key, get_menus_for_day
from operating_hours import (
    now_wita, resolve_delivery, get_delivery_date,
    is_order_window_open, closed_message, format_operating_info,
)
from holiday_service import is_holiday_mode

# ── MENU (Supabase) ──────────────────────────────────────────────────────────

def _fetch_daily_menus() -> list[dict]:
    return get_menus_for_day(supabase, get_day_key())


def _check_order_gate(member: dict) -> str | None:
    """Return pesan penolakan jika order diblokir, else None."""
    active, holiday_msg = is_holiday_mode(supabase)
    if active:
        return holiday_msg

    now = now_wita()
    open_ok, reason = is_order_window_open(now)
    if not open_ok:
        return closed_message(member.get("nama", "Member"), reason or "after_close")
    return None

def _build_rekomendasi(daftar_menu: list[dict], kalori_target: int, goal_type: str) -> list[dict]:
    rekomendasi = []
    for menu in daftar_menu:
        m     = kalori_target / menu["kalori_std"]
        items = [scale_item(i, m) for i in menu["detail_item_std"]]
        tn    = total_nutrition(items)
        rekomendasi.append({
            "nama_menu":        menu["nama"],
            "porsi_gram":       sum(i["porsi_gram"] for i in items),
            "protein_gram":     tn["protein_gram"],
            "karbo_gram":       tn["karbo_gram"],
            "harga_final":      round(menu["harga_std"] * m),
            "keterangan_harga": build_price_note(m, goal_type),
            "detail_item":      items,
            "total_nutrisi":    tn,
            "menu_template_id": menu.get("id"),
        })
    return rekomendasi

ACTIVITY_TYPES = {
    "1": ("lari",   "🏃"),
    "2": ("sepeda", "🚴"),
    "3": ("renang", "🏊"),
    "4": ("gym",    "🏋️"),
}

KALORI_PER_KG_KM = {"lari": 1.036, "sepeda": 0.5, "renang": 0.7}

STRAVA_TYPE_MAP: dict[str, str] = {
    "Run": "lari", "VirtualRun": "lari", "TrailRun": "lari",
    "Ride": "sepeda", "VirtualRide": "sepeda", "MountainBikeRide": "sepeda", "GravelRide": "sepeda",
    "Swim": "renang", "OpenWaterSwim": "renang",
    "WeightTraining": "gym", "Crossfit": "gym", "Workout": "gym",
    "Elliptical": "gym", "StairStepper": "gym", "HIIT": "gym",
}

SPORT_EMOJI = {"lari": "🏃", "sepeda": "🚴", "renang": "🏊", "gym": "🏋️", "unknown": "❓"}

ORDER_TIMEOUT_SEC = 600  # 10 menit

# Per-user in-memory session (chat_id → data)
member_sessions:    dict[str, dict]  = {}
daftar_state:       dict[str, dict]  = {}
order_state:        dict[str, dict]  = {}
order_state_ts:     dict[str, float] = {}   # timestamp terakhir aktif
manual_pref_count:  dict[str, int]   = {}   # hitung berapa kali pilih manual vs Strava

# ── SUPABASE HELPERS ───────────────────────────────────────────────────────────

def get_member(chat_id: str) -> dict | None:
    r = supabase.table("user_profile").select("*").eq("telegram_chat_id", chat_id).limit(1).execute()
    return r.data[0] if r.data else None

def get_measurement(user_id: int) -> dict | None:
    r = supabase.table("user_measurements").select("*").eq("user_id", user_id).order("recorded_at", desc=True).limit(1).execute()
    return r.data[0] if r.data else None

def get_goal(user_id: int) -> dict | None:
    r = supabase.table("user_goals").select("*").eq("user_id", user_id).order("aktif_dari", desc=True).limit(1).execute()
    return r.data[0] if r.data else None

def get_berat(user_id: int) -> float:
    m = get_measurement(user_id)
    return float(m["berat_kg"]) if m and m.get("berat_kg") else 65.0

def get_goal_info(user_id: int) -> tuple[str, float]:
    g = get_goal(user_id)
    return (g["goal_type"], float(g["modifier"])) if g else ("maintenance", 1.0)

def calculate_age(tanggal_lahir_str: str) -> int:
    tgl = datetime.date.fromisoformat(tanggal_lahir_str)
    today = datetime.date.today()
    return today.year - tgl.year - ((today.month, today.day) < (tgl.month, tgl.day))

def is_active_member(chat_id: str) -> tuple[bool, dict | None]:
    m = get_member(chat_id)
    if not m:
        return False, None
    return m["status"] == "aktif", m

def notify_telegram(chat_id: str, text: str):
    """Kirim pesan Telegram tanpa polling (dipakai dari main.py callback)."""
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=5
    )

# ── STRAVA ─────────────────────────────────────────────────────────────────────

def get_strava_token(refresh_token: str) -> str | None:
    r = requests.post("https://www.strava.com/oauth/token", data={
        "client_id":     STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type":    "refresh_token",
    }, timeout=10)
    return r.json().get("access_token") if r.status_code == 200 else None

def fetch_recent_activities(access_token: str, hours: int = 24) -> list:
    """Ambil aktivitas dalam N jam terakhir (max 5)."""
    since = int((datetime.datetime.now() - datetime.timedelta(hours=hours)).timestamp())
    r = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"after": since, "per_page": 5},
        timeout=10
    )
    return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []

def fetch_fallback_activity(access_token: str) -> list:
    """Ambil 1 aktivitas terakhir tanpa batasan waktu (fallback)."""
    r = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"per_page": 1},
        timeout=10
    )
    return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []

def process_strava_activity(raw: dict, berat_kg: float) -> dict:
    """Mapping satu aktivitas Strava ke format internal."""
    strava_type = raw.get("type", "")
    jenis = STRAVA_TYPE_MAP.get(strava_type, "unknown")
    jarak_km    = round(raw.get("distance", 0) / 1000, 2)
    waktu_menit = round(raw.get("moving_time", 0) / 60)
    a = {
        "strava_id":   raw.get("id"),
        "nama":        raw.get("name", "Aktivitas"),
        "jenis":       jenis,
        "strava_type": strava_type,
        "jarak_km":    jarak_km,
        "waktu_menit": waktu_menit,
        "start_time":  raw.get("start_date_local", ""),
    }
    a["kalori_est"] = estimate_calories(a, berat_kg) if jenis != "unknown" else 0
    return a

def _fmt_activity_time(iso_str: str) -> str:
    """Format waktu Strava ke human-readable (X mnt/jam lalu)."""
    try:
        dt  = datetime.datetime.fromisoformat(iso_str)
        now = datetime.datetime.now()
        diff_sec = (now - dt).total_seconds()
        if diff_sec < 3600:
            return f"{int(diff_sec / 60)} menit lalu"
        if diff_sec < 86400:
            return f"{int(diff_sec / 3600)} jam lalu"
        return dt.strftime("%d %b %H:%M")
    except Exception:
        return iso_str[:10] if iso_str else "-"

def build_activity_selection(activities: list) -> tuple[str, "InlineKeyboardMarkup"]:
    """Bangun pesan + tombol pilih aktivitas Strava."""
    if len(activities) == 1:
        a   = activities[0]
        em  = SPORT_EMOJI.get(a["jenis"], "❓")
        ts  = _fmt_activity_time(a["start_time"])
        dist = f"{a['jarak_km']} km · " if a["jarak_km"] > 0 else ""
        kal  = f"~{a['kalori_est']} kkal" if a["kalori_est"] > 0 else "tipe tidak dikenal"
        msg = (
            f"📍 <b>Aktivitas Terbaru di Strava</b>\n\n"
            f"{em} <b>{html.escape(a['nama'])}</b>\n"
            f"{dist}{a['waktu_menit']} mnt · {ts}\n"
            f"Est. terbakar: <b>{kal}</b>\n\n"
            f"Pakai aktivitas ini untuk ordermu?"
        )
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✅ Ya, pakai ini",  callback_data="ord_pick:0"))
        markup.add(InlineKeyboardButton("✏️ Input Manual",   callback_data="ord_manual"))
    else:
        known_kal = sum(a["kalori_est"] for a in activities if a["jenis"] != "unknown")
        msg = f"📍 <b>{len(activities)} Sesi (24 jam terakhir)</b>\n\n"
        for a in activities:
            em   = SPORT_EMOJI.get(a["jenis"], "❓")
            ts   = _fmt_activity_time(a["start_time"])
            dist = f"{a['jarak_km']} km · " if a["jarak_km"] > 0 else ""
            kal  = f"~{a['kalori_est']} kkal" if a["kalori_est"] > 0 else "tipe tidak dikenal"
            msg += f"{em} <b>{html.escape(a['nama'])}</b> · {ts}\n  {dist}{a['waktu_menit']} mnt · {kal}\n\n"
        msg += "Pilih sesi untuk order ini:"
        markup = InlineKeyboardMarkup()
        for i, a in enumerate(activities):
            em  = SPORT_EMOJI.get(a["jenis"], "❓")
            kal = f"~{a['kalori_est']} kkal" if a["kalori_est"] > 0 else "?"
            markup.add(InlineKeyboardButton(
                f"{em} {a['nama']} ({kal})", callback_data=f"ord_pick:{i}"
            ))
        if known_kal > 0 and sum(1 for a in activities if a["jenis"] != "unknown") > 1:
            markup.add(InlineKeyboardButton(
                f"🔥 Gabung semua (~{known_kal} kkal)", callback_data="ord_combine"
            ))
        markup.add(InlineKeyboardButton("✏️ Input Manual", callback_data="ord_manual"))
    return msg, markup

def _send_manual_activity_picker(chat_id: str):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton("🏃 Lari"), KeyboardButton("🚴 Sepeda"))
    markup.row(KeyboardButton("🏊 Renang"), KeyboardButton("🏋️ Gym"))
    msg = bot.send_message(chat_id,
        "✏️ <b>Input Aktivitas Manual</b>\n\nOlahraga apa yang baru kamu lakukan?",
        parse_mode="HTML", reply_markup=markup)
    bot.register_next_step_handler(msg, step_order_activity_picker)

def step_order_activity_picker(message):
    chat_id = str(message.from_user.id)
    ok, member = is_active_member(chat_id)
    if not ok:
        bot.send_message(chat_id, "❌ Akses ditolak.", reply_markup=ReplyKeyboardRemove())
        return
    text = (message.text or "").strip().lower()
    activity_map = {"lari": "lari", "sepeda": "sepeda", "renang": "renang", "gym": "gym"}
    jenis = next((k for k in activity_map if k in text), None)
    if jenis is None:
        markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.row(KeyboardButton("🏃 Lari"), KeyboardButton("🚴 Sepeda"))
        markup.row(KeyboardButton("🏊 Renang"), KeyboardButton("🏋️ Gym"))
        msg = bot.send_message(chat_id, "❌ Pilih aktivitas dari tombol di bawah:", reply_markup=markup)
        bot.register_next_step_handler(msg, step_order_activity_picker)
        return
    if not is_order_fresh(chat_id):
        order_state[chat_id] = {"member": member}
    order_state[chat_id]["jenis"] = jenis
    emoji = SPORT_EMOJI.get(jenis, "🏃")
    touch_order(chat_id)
    if jenis == "gym":
        msg = bot.send_message(chat_id,
            "🏋️ <b>Durasi Gym</b>\n\nBerapa lama sesi gym kamu?\n"
            "Masukkan dalam menit. Contoh: <code>60</code>",
            parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        bot.register_next_step_handler(msg, step_order_durasi_gym)
    else:
        msg = bot.send_message(chat_id,
            f"{emoji} <b>Jarak Tempuh</b>\n\nSeberapa jauh kamu {jenis}?\n"
            f"Masukkan dalam km. Contoh: <code>5.2</code>",
            parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        bot.register_next_step_handler(msg, step_order_jarak)

def _ask_unknown_type(chat_id: str, activity: dict):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🏃 Lari",        callback_data="ord_retype:lari"),
        InlineKeyboardButton("🚴 Sepeda",      callback_data="ord_retype:sepeda"),
        InlineKeyboardButton("🏊 Renang",      callback_data="ord_retype:renang"),
        InlineKeyboardButton("🏋️ Gym/Latihan", callback_data="ord_retype:gym"),
        InlineKeyboardButton("✏️ Input Manual", callback_data="ord_manual"),
    )
    bot.send_message(chat_id,
        f"❓ <b>Tipe aktivitas tidak dikenali</b>\n\n"
        f"Aktivitas <b>'{html.escape(activity['nama'])}'</b> "
        f"(tipe Strava: {activity.get('strava_type', '-')}) tidak ada di daftar kami.\n\n"
        f"Ini termasuk kategori apa?",
        parse_mode="HTML", reply_markup=markup)

def is_order_fresh(chat_id: str) -> bool:
    ts = order_state_ts.get(chat_id, 0)
    if (time.time() - ts) > ORDER_TIMEOUT_SEC:
        order_state.pop(chat_id, None)
        order_state_ts.pop(chat_id, None)
        return False
    return True

def touch_order(chat_id: str):
    order_state_ts[chat_id] = time.time()

def _track_manual_pref(chat_id: str, member_id: int):
    """Setelah 2× user pilih manual daripada Strava, set flag prefers_manual."""
    manual_pref_count[chat_id] = manual_pref_count.get(chat_id, 0) + 1
    if manual_pref_count[chat_id] >= 2:
        try:
            supabase.table("user_profile").update(
                {"prefers_manual": True}
            ).eq("id", member_id).execute()
        except Exception:
            pass

# ── KALORI & MENU CALCULATION ──────────────────────────────────────────────────

def estimate_calories(activity: dict, berat_kg: float) -> int:
    jenis = activity.get("jenis", "lari")
    if jenis == "gym":
        return round(berat_kg * 0.07 * activity.get("waktu_menit", 0))
    return round(activity.get("jarak_km", 0) * berat_kg * KALORI_PER_KG_KM.get(jenis, 1.036))

def scale_item(item: dict, m: float) -> dict:
    return {
        "nama_item":    item["nama_item"],
        "porsi_gram":   round(item["porsi_gram"] * m),
        "kalori":       round(item["kalori"] * m),
        "protein_gram": round(item["protein_gram"] * m, 1),
        "karbo_gram":   round(item["karbo_gram"] * m, 1),
        "lemak_gram":   round(item["lemak_gram"] * m, 1),
    }

def total_nutrition(items: list) -> dict:
    return {
        "kalori":       sum(i["kalori"] for i in items),
        "protein_gram": round(sum(i["protein_gram"] for i in items), 1),
        "karbo_gram":   round(sum(i["karbo_gram"] for i in items), 1),
        "lemak_gram":   round(sum(i["lemak_gram"] for i in items), 1),
    }

def build_price_note(multiplier: float, goal_type: str) -> str:
    pct = round((multiplier - 1) * 100, 1)
    label = {"maintenance": "maintenance", "deficit": "defisit", "surplus": "surplus"}.get(goal_type, goal_type)
    if pct > 0: return f"Porsi +{pct}% dari standar. Goal: {label}."
    if pct < 0: return f"Porsi {pct}% dari standar. Goal: {label}."
    return f"Porsi standar. Goal: {label}."

def analyze(activity: dict, member: dict) -> dict:
    user_id  = member["id"]
    berat_kg = get_berat(user_id)
    goal_type, modifier = get_goal_info(user_id)

    daftar_menu = _fetch_daily_menus()
    kalori_terbakar = estimate_calories(activity, berat_kg)
    kalori_target   = round(kalori_terbakar * modifier)
    rekomendasi = _build_rekomendasi(daftar_menu, kalori_target, goal_type)

    jenis = activity.get("jenis", "lari")
    if jenis == "gym":
        aktivitas_str = f"Gym {activity.get('waktu_menit', 0):.0f} menit | Est. {kalori_terbakar} kkal terbakar"
    else:
        aktivitas_str = (
            f"{jenis.capitalize()} {activity.get('jarak_km', 0):.2f} km | "
            f"{activity.get('waktu_menit', 0):.0f} menit | Est. {kalori_terbakar} kkal terbakar"
        )

    return {
        "aktivitas":       aktivitas_str,
        "rekomendasi":     rekomendasi,
        "berat_kg":        berat_kg,
        "goal_type":       goal_type,
        "kalori_terbakar": kalori_terbakar,
        "kalori_target":   kalori_target,
    }

# ── ORDER ──────────────────────────────────────────────────────────────────────

def execute_order(payload: dict, member_nama: str, telegram_chat_id: str) -> bool:
    try:
        r = requests.post(
            "http://127.0.0.1:8000/api/order",
            json={**payload, "member_nama": member_nama, "telegram_chat_id": telegram_chat_id},
            headers={"X-API-Key": INTERNAL_API_KEY},
            timeout=10
        )
        return r.status_code == 200
    except Exception:
        return False


def _submit_order(chat_id: str, member: dict, current: dict, menu: dict) -> bool:
    now = now_wita()
    slot = resolve_delivery(now)
    delivery_date = get_delivery_date(now).isoformat()
    payload = {
        "aktivitas":            current["aktivitas"],
        "menu_pilihan":         menu,
        "delivery_slot":        slot,
        "delivery_date":        delivery_date,
        "menu_template_id":     menu.get("menu_template_id"),
        "is_sold_out_at_order": bool(menu.get("is_sold_out")),
    }
    return execute_order(payload, member["nama"], chat_id)

# ── MESSAGE BUILDERS ───────────────────────────────────────────────────────────

def fmt(v, d=0):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "-"
    if d == 0:
        return f"{int(round(n)):,}".replace(",", ".")
    return f"{n:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")

def fmt_rp(v):
    return f"Rp {fmt(v)}"

def get_total(menu: dict) -> dict:
    t = menu.get("total_nutrisi") or {}
    if t:
        return t
    items = menu.get("detail_item") or []
    return {k: sum(i.get(k, 0) for i in items)
            for k in ["kalori", "protein_gram", "karbo_gram", "lemak_gram"]}

def build_options_message(k: dict, member_nama: str) -> str:
    gt    = k.get("goal_type", "maintenance")
    berat = k.get("berat_kg", 65)
    kt    = k.get("kalori_terbakar", 0)
    ktar  = k.get("kalori_target", 0)
    emoji = {"maintenance": "⚖️", "deficit": "📉", "surplus": "📈"}.get(gt, "⚖️")
    pct   = round((ktar / kt - 1) * 100) if kt else 0
    sign  = f"+{pct}" if pct > 0 else str(pct)

    pesan = (
        f"👋 Halo <b>{html.escape(member_nama)}</b>\n\n"
        f"{html.escape(k['aktivitas'])}\n"
        f"{emoji} Berat: {berat}kg | {emoji} {gt} ({sign}%) → target <b>{fmt(ktar)} kkal</b>\n\n"
        f"🎯 <b>OPSI RECOVERY HARI INI</b>\n"
    )
    for i, menu in enumerate(k["rekomendasi"]):
        tn = get_total(menu)
        sold_out = " — 🔴 <b>SOLD OUT</b>" if menu.get("is_sold_out") else ""
        pesan += (
            f"{i+1}. {html.escape(menu['nama_menu'])}{sold_out}\n"
            f"({fmt(menu['porsi_gram'])}g - {fmt(tn.get('kalori'))}kkal)\n"
            f"🥩 Protein {fmt(tn.get('protein_gram'), 1)}g | 🍚 Karbo {fmt(tn.get('karbo_gram'), 1)}g\n"
            f"{fmt_rp(menu['harga_final'])}\n\n"
        )
    pesan += "Tap tombol di bawah untuk lihat detail breakdown."
    return pesan

def build_options_markup(k: dict) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    for i, menu in enumerate(k["rekomendasi"]):
        markup.add(InlineKeyboardButton(
            f"Lihat Detail {i+1}: {menu['nama_menu']}", callback_data=f"detail:{i}"
        ))
    return markup

def build_detail_markup(idx: int, menu: dict | None = None) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    if menu and menu.get("is_sold_out"):
        markup.add(InlineKeyboardButton("🔴 Sold Out", callback_data="soldout"))
    else:
        markup.add(InlineKeyboardButton("✅ Order menu ini", callback_data=f"order:{idx}"))
    markup.add(InlineKeyboardButton("⬅️ Lihat menu lain", callback_data="back"))
    return markup

def build_detail_message(activity: str, menu: dict, is_ordered: bool = False) -> str:
    tn     = get_total(menu)
    status = "✅ <b>ORDER DIKONFIRMASI</b>" if is_ordered else "🔎 <b>DETAIL MENU</b>"
    pesan  = f"{status}\n{html.escape(menu['nama_menu'])}\n\n"
    pesan += f"{html.escape(activity)}\n"
    pesan += f"<b>Harga:</b> {fmt_rp(menu['harga_final'])}\n"
    pesan += f"{html.escape(menu.get('keterangan_harga', ''))}\n\n"
    pesan += "<b>Breakdown item & nutrisi</b>\n"
    for item in (menu.get("detail_item") or []):
        pesan += (
            f"• {html.escape(item.get('nama_item', ''))} ({fmt(item.get('porsi_gram'))}g)\n"
            f"  {fmt(item.get('kalori'))} kkal\n"
            f"  {fmt(item.get('protein_gram'), 1)}g protein\n"
            f"  {fmt(item.get('karbo_gram'), 1)}g karbo\n"
            f"  {fmt(item.get('lemak_gram'), 1)}g lemak\n\n"
        )
    pesan += (
        f"<b>Total</b>\n"
        f"{fmt(tn.get('kalori'))} kkal\n"
        f"{fmt(tn.get('protein_gram'), 1)}g protein\n"
        f"{fmt(tn.get('karbo_gram'), 1)}g karbo\n"
        f"{fmt(tn.get('lemak_gram'), 1)}g lemak\n\n"
    )
    pesan += "Pesanan dikirim ke dapur Sassyroll." if is_ordered else "Cocok? Tekan tombol order di bawah."
    return pesan

# ── BOT ────────────────────────────────────────────────────────────────────────

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Persistent keyboard yang selalu muncul di bawah chat."""
    kb = ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True)
    kb.row(KeyboardButton("🛒 Order"), KeyboardButton("👤 Profil"))
    kb.row(KeyboardButton("🔗 Strava"), KeyboardButton("❓ Help"))
    return kb


def _restore_main_keyboard(chat_id: str):
    """Kembalikan tombol shortcut di bawah chat setelah flow inline selesai."""
    bot.send_message(
        chat_id,
        "Gunakan tombol di bawah untuk order lagi, cek profil, atau bantuan.",
        reply_markup=get_main_keyboard(),
    )


def _finish_order_message(chat_id: str, call, detail_text: str):
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=detail_text,
        parse_mode="HTML",
    )
    _restore_main_keyboard(chat_id)

def _step_bar(step: int, total: int = 6) -> str:
    return f"Langkah {step} dari {total}  {'▓' * step}{'░' * (total - step)}"

DAFTAR_STEPS = frozenset({"nama", "tgl", "gender", "berat", "tinggi", "goal"})

GOAL_OPTIONS = {
    "maintenance": ("maintenance", 1.0, "⚖️ Maintenance"),
    "deficit":     ("deficit",     0.8, "📉 Defisit (-20%)"),
    "surplus":     ("surplus",     1.2, "📈 Surplus (+20%)"),
}

def _set_daftar_step(chat_id: str, step: str | None):
    if chat_id not in daftar_state:
        daftar_state[chat_id] = {}
    if step:
        daftar_state[chat_id]["_step"] = step
    else:
        daftar_state[chat_id].pop("_step", None)

def _daftar_waiting(message) -> bool:
    cid = str(message.from_user.id)
    return daftar_state.get(cid, {}).get("_step") in DAFTAR_STEPS and bool(message.text)

def _parse_gender(text: str) -> str | None:
    t = (text or "").strip()
    if t.upper() in ("L", "P"):
        return t.upper()
    low = t.lower()
    if "laki" in low:
        return "L"
    if "perempuan" in low or "wanita" in low:
        return "P"
    return None

def _prompt_daftar_berat(chat_id: str):
    bot.send_message(chat_id,
        f"<b>{_step_bar(4)}</b>\n\n"
        "⚖️ <b>Berapa berat badanmu sekarang?</b> (dalam kg)\n\n"
        "Contoh: <code>70</code> atau <code>70.5</code>\n\n"
        "<i>Berat badan adalah faktor utama menghitung kalori yang terbakar saat olahraga. "
        "Kamu bisa update kapan saja dengan /berat</i>",
        parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
    _set_daftar_step(chat_id, "berat")

def _parse_goal(text: str) -> str | None:
    low = (text or "").strip().lower()
    if "maintenance" in low or "maint" in low:
        return "maintenance"
    if "defisit" in low or "deficit" in low or "fat loss" in low:
        return "deficit"
    if "surplus" in low or "mass gain" in low:
        return "surplus"
    return None

def _prompt_daftar_goal(chat_id: str):
    _set_daftar_step(chat_id, "goal")
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.row(KeyboardButton("⚖️ Maintenance"))
    markup.row(KeyboardButton("📉 Defisit (-20%)"))
    markup.row(KeyboardButton("📈 Surplus (+20%)"))
    bot.send_message(chat_id,
        f"<b>{_step_bar(6)}</b> — Langkah terakhir!\n\n"
        "🎯 <b>Apa goal kesehatanmu saat ini?</b>\n\n"
        "<i>Tap salah satu tombol di bawah. Kamu bisa mengubahnya kapan saja dengan /goal</i>",
        parse_mode="HTML", reply_markup=markup)

def _show_daftar_ringkasan(chat_id: str):
    data = daftar_state[chat_id]
    usia = calculate_age(data.get("tanggal_lahir", "2000-01-01"))
    gender_label = "Laki-laki" if data.get("jenis_kelamin") == "L" else "Perempuan"
    tgl_fmt = data.get("tanggal_lahir", "").replace("-", "/")
    goal_label_plain = {
        "maintenance": "Maintenance",
        "deficit": "Defisit (-20%)",
        "surplus": "Surplus (+20%)",
    }.get(data.get("goal_type"), data.get("goal_type", ""))
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ Daftar Sekarang", callback_data="reg_confirm:yes"),
        InlineKeyboardButton("🔄 Ulangi dari Awal", callback_data="reg_confirm:no"),
    )
    _set_daftar_step(chat_id, None)
    bot.send_message(chat_id,
        f"📋 <b>Ringkasan Pendaftaran</b>\n\n"
        f"<code>"
        f"Nama    : {data.get('nama', '')}\n"
        f"Lahir   : {tgl_fmt} ({usia} thn)\n"
        f"Gender  : {gender_label}\n"
        f"Berat   : {data.get('berat_kg')} kg\n"
        f"Tinggi  : {data.get('tinggi_cm')} cm\n"
        f"Goal    : {goal_label_plain}"
        f"</code>\n\n"
        "Semua data sudah benar?",
        parse_mode="HTML", reply_markup=markup)

# ── /start /help ───────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start", "help"])
def cmd_help(message):
    chat_id = str(message.from_user.id)
    member  = get_member(chat_id)

    if not member:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🚀 Daftar Sekarang", callback_data="reg_cta"))
        bot.send_message(chat_id,
            "👋 Halo! Selamat datang di <b>Sassyroll Healthy Catering</b>.\n\n"
            "Kami menyesuaikan menu makan sehat berdasarkan:\n"
            "• Aktivitas fisikmu hari ini 🏃\n"
            "• Berat badan & target kesehatanmu ⚖️\n"
            "• Menu rotasi mingguan bergizi tinggi 🥗\n\n"
            "Daftar dulu untuk mulai — hanya butuh <b>~2 menit</b>!",
            parse_mode="HTML", reply_markup=markup)
        return

    if member["status"] == "pending":
        bot.send_message(chat_id,
            "⏳ <b>Pendaftaranmu sedang diproses</b>\n\n"
            "Admin akan segera mereview dan mengaktifkan akunmu.\n"
            "Kamu akan dapat notifikasi otomatis setelah disetujui.\n\n"
            "Sambil menunggu, kamu bisa hubungkan Strava:\n"
            "/hubungkan_strava",
            parse_mode="HTML")
        return
    if member["status"] == "nonaktif":
        bot.send_message(chat_id,
            "❌ <b>Akunmu tidak aktif</b>\n\nHubungi admin untuk mengaktifkan kembali.",
            parse_mode="HTML")
        return

    strava = "✅ Terhubung" if member.get("strava_connected") else "❌ Belum → /hubungkan_strava"
    teks = (
        f"🍱 <b>Sassyroll Healthy Catering</b>\n\n"
        f"<b>🛒 Order</b>\n"
        f"/order — pesan menu setelah olahraga\n\n"
        f"<b>👤 Profilku</b>\n"
        f"/profil — lihat data & goal saat ini\n"
        f"/berat 72 — update berat badan (kg)\n"
        f"/tinggi 173 — update tinggi badan (cm)\n"
        f"/goal defisit | maintenance | surplus\n\n"
        f"<b>🔗 Strava:</b> {strava}\n"
        f"/hubungkan_strava — otomatisasi input aktivitas\n\n"
        f"{format_operating_info()}"
    )
    inline = None
    if UX_MODE == "C":
        inline = InlineKeyboardMarkup(row_width=2)
        inline.add(
            InlineKeyboardButton("🛒 Order Sekarang", callback_data="shortcut_order"),
            InlineKeyboardButton("👤 Lihat Profil",   callback_data="shortcut_profil"),
        )
        if not member.get("strava_connected"):
            inline.add(InlineKeyboardButton("🔗 Hubungkan Strava", callback_data="shortcut_strava"))
    bot.send_message(chat_id, teks, parse_mode="HTML",
                     reply_markup=inline or get_main_keyboard())

# ── Keyboard button handlers ───────────────────────────────────────────────────

KEYBOARD_BUTTONS = {"🛒 Order", "👤 Profil", "🔗 Strava", "❓ Help"}

@bot.message_handler(func=_daftar_waiting)
def route_daftar_message(message):
    """Tangkap input pendaftaran via state (lebih andal dari next_step saja)."""
    cid = str(message.from_user.id)
    step = daftar_state[cid]["_step"]
    {
        "nama":   step_daftar_nama,
        "tgl":    step_daftar_tgl,
        "gender": step_daftar_gender,
        "berat":  step_daftar_berat,
        "tinggi": step_daftar_tinggi,
        "goal":   step_daftar_goal,
    }[step](message)

@bot.message_handler(func=lambda m: m.text in KEYBOARD_BUTTONS)
def handle_keyboard_button(message):
    t = message.text
    if t == "🛒 Order":
        cmd_order(message)
    elif t == "👤 Profil":
        cmd_profil(message)
    elif t == "🔗 Strava":
        cmd_hubungkan_strava(message)
    elif t == "❓ Help":
        cmd_help(message)

# ── /daftar ────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["daftar"])
def cmd_daftar(message):
    chat_id = str(message.from_user.id)
    if get_member(chat_id):
        bot.send_message(chat_id,
            "ℹ️ Kamu sudah terdaftar.\nKetik /help untuk melihat menu.",
            parse_mode="HTML")
        return
    daftar_state[chat_id] = {}
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🚀 Mulai Daftar", callback_data="reg_start"))
    bot.send_message(chat_id,
        "📋 <b>Pendaftaran Member Sassyroll</b>\n\n"
        "Kami butuh beberapa data untuk menyesuaikan menu dan porsi makananmu secara personal.\n\n"
        "Prosesnya hanya <b>~2 menit</b> dan cukup dilakukan sekali.\n\n"
        "🕕 Jam order: 06:00 – 18:00 WITA\n"
        "🚚 Pengiriman: 09:00, 12:00, 15:00 & 18:00 WITA",
        parse_mode="HTML", reply_markup=markup)

def step_daftar_nama(message):
    chat_id = str(message.from_user.id)
    if chat_id not in daftar_state:
        bot.send_message(chat_id, "❌ Sesi habis. Ketik /daftar untuk mulai ulang.")
        return
    _set_daftar_step(chat_id, "nama")
    nama = message.text.strip()
    if len(nama) < 2:
        bot.send_message(chat_id, "❌ Nama terlalu pendek. Coba lagi:")
        _set_daftar_step(chat_id, "nama")
        return
    daftar_state[chat_id]["nama"] = nama
    _set_daftar_step(chat_id, "tgl")
    bot.send_message(chat_id,
        f"<b>{_step_bar(2)}</b>\n\n"
        "📅 <b>Kapan tanggal lahirmu?</b>\n\n"
        "Format: <code>DD/MM/YYYY</code>\n"
        "Contoh: <code>15/08/1997</code>\n\n"
        "<i>Umurmu dipakai untuk menghitung kebutuhan kalori yang lebih akurat.</i>",
        parse_mode="HTML")

def step_daftar_tgl(message):
    chat_id = str(message.from_user.id)
    try:
        tgl = datetime.datetime.strptime(message.text.strip(), "%d/%m/%Y").date()
        if tgl.year < 1930 or tgl >= datetime.date.today(): raise ValueError
        daftar_state[chat_id]["tanggal_lahir"] = tgl.isoformat()
        _set_daftar_step(chat_id, "gender")
        markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.row(KeyboardButton("👨 Laki-laki"), KeyboardButton("👩 Perempuan"))
        bot.send_message(chat_id,
            f"<b>{_step_bar(3)}</b>\n\n"
            "⚧ <b>Jenis kelaminmu?</b>\n\n"
            "<i>Tap salah satu tombol di bawah.</i>",
            parse_mode="HTML", reply_markup=markup)
    except ValueError:
        bot.send_message(chat_id,
            "❌ Format tanggal tidak valid.\n"
            "Coba lagi: <code>DD/MM/YYYY</code>\n"
            "Contoh: <code>15/08/1997</code>",
            parse_mode="HTML")
        _set_daftar_step(chat_id, "tgl")

def step_daftar_gender(message):
    chat_id = str(message.from_user.id)
    if chat_id not in daftar_state:
        bot.send_message(chat_id, "❌ Sesi habis. Ketik /daftar untuk mulai ulang.",
            reply_markup=ReplyKeyboardRemove())
        return
    jenis_kelamin = _parse_gender(message.text or "")
    if not jenis_kelamin:
        markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.row(KeyboardButton("👨 Laki-laki"), KeyboardButton("👩 Perempuan"))
        bot.send_message(chat_id,
            "❌ Pilih salah satu dari tombol di bawah.",
            reply_markup=markup)
        _set_daftar_step(chat_id, "gender")
        return
    daftar_state[chat_id]["jenis_kelamin"] = jenis_kelamin
    _prompt_daftar_berat(chat_id)

def step_daftar_berat(message):
    chat_id = str(message.from_user.id)
    try:
        berat = float(message.text.strip().replace(",", "."))
        if not (30 <= berat <= 200): raise ValueError
        daftar_state[chat_id]["berat_kg"] = berat
        _set_daftar_step(chat_id, "tinggi")
        bot.send_message(chat_id,
            f"<b>{_step_bar(5)}</b>\n\n"
            "📏 <b>Berapa tinggi badanmu?</b> (dalam cm)\n\n"
            "Contoh: <code>172</code>\n\n"
            "<i>Dipakai bersama berat untuk menghitung BMI dan kebutuhan energimu.</i>",
            parse_mode="HTML")
    except ValueError:
        bot.send_message(chat_id, "❌ Berat tidak valid (30–200 kg). Coba lagi:")
        _set_daftar_step(chat_id, "berat")

def step_daftar_tinggi(message):
    chat_id = str(message.from_user.id)
    try:
        tinggi = float(message.text.strip().replace(",", "."))
        if not (100 <= tinggi <= 250): raise ValueError
        daftar_state[chat_id]["tinggi_cm"] = tinggi
        _prompt_daftar_goal(chat_id)
    except ValueError:
        bot.send_message(chat_id, "❌ Tinggi tidak valid (100–250 cm). Coba lagi:")
        _set_daftar_step(chat_id, "tinggi")

def step_daftar_goal(message):
    chat_id = str(message.from_user.id)
    if chat_id not in daftar_state:
        bot.send_message(chat_id, "❌ Sesi habis. Ketik /daftar untuk mulai ulang.",
            reply_markup=ReplyKeyboardRemove())
        return
    goal_key = _parse_goal(message.text or "")
    if not goal_key or goal_key not in GOAL_OPTIONS:
        bot.send_message(chat_id, "❌ Pilih salah satu dari tombol di bawah.")
        _prompt_daftar_goal(chat_id)
        return
    goal_type, modifier, _ = GOAL_OPTIONS[goal_key]
    daftar_state[chat_id].update({"goal_type": goal_type, "modifier": modifier})
    _show_daftar_ringkasan(chat_id)

def _simpan_member(call, chat_id: str):
    """Simpan data registrasi ke Supabase dan kirim notifikasi."""
    data = daftar_state.get(chat_id, {})
    required = {"nama", "tanggal_lahir", "jenis_kelamin", "berat_kg", "tinggi_cm", "goal_type", "modifier"}
    if not required.issubset(data.keys()):
        bot.answer_callback_query(call.id, "Data tidak lengkap. Ketik /daftar ulang.")
        return
    try:
        bot.answer_callback_query(call.id, "⏳ Mendaftarkan...")
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass

        result = supabase.table("user_profile").insert({
            "nama":             data["nama"],
            "tanggal_lahir":    data["tanggal_lahir"],
            "jenis_kelamin":    data["jenis_kelamin"],
            "telegram_chat_id": chat_id,
            "status":           "pending",
        }).execute()
        user_id = result.data[0]["id"]

        supabase.table("user_measurements").insert({
            "user_id":   user_id,
            "berat_kg":  data["berat_kg"],
            "tinggi_cm": data["tinggi_cm"],
            "sumber":    "telegram_daftar",
        }).execute()

        supabase.table("user_goals").insert({
            "user_id":    user_id,
            "goal_type":  data["goal_type"],
            "modifier":   data["modifier"],
            "aktif_dari": datetime.datetime.now().isoformat(),
        }).execute()

        bot.send_message(chat_id,
            f"🎉 <b>Pendaftaran Berhasil!</b>\n\n"
            f"Halo <b>{html.escape(data['nama'])}</b>! Datamu sudah kami terima.\n\n"
            f"<b>Apa yang terjadi selanjutnya?</b>\n"
            f"1️⃣ Admin akan mereview pendaftaranmu\n"
            f"2️⃣ Kamu dapat notifikasi setelah disetujui\n"
            f"3️⃣ Setelah aktif, langsung bisa <code>/order</code>\n\n"
            f"<i>Estimasi persetujuan: dalam 1×24 jam.</i>\n\n"
            f"{format_operating_info()}\n\n"
            f"Sambil menunggu, hubungkan Strava-mu agar order nanti lebih otomatis:\n"
            f"/hubungkan_strava",
            parse_mode="HTML", reply_markup=ReplyKeyboardRemove())

        daftar_state.pop(chat_id, None)
    except Exception as e:
        bot.send_message(chat_id,
            f"❌ Gagal mendaftar. Coba lagi dengan /daftar\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML")

# ── /hubungkan_strava ──────────────────────────────────────────────────────────

@bot.message_handler(commands=["hubungkan_strava"])
def cmd_hubungkan_strava(message):
    chat_id = str(message.from_user.id)
    ok, member = is_active_member(chat_id)
    if not ok:
        bot.send_message(chat_id, "⚠️ Kamu perlu terdaftar dan aktif untuk menghubungkan Strava.")
        return

    auth_url = f"{BASE_URL}/strava/auth/{chat_id}"
    bot.send_message(chat_id,
        f"🔗 <b>Hubungkan Akun Strava</b>\n\n"
        f"1. Klik link di bawah\n"
        f"2. Login ke Strava & izinkan akses\n"
        f"3. Bot otomatis terhubung!\n\n"
        f"<b>Link:</b> {auth_url}\n\n"
        f"<i>⚠️ Link hanya berfungsi jika server bisa diakses publik (bukan localhost).</i>",
        parse_mode="HTML")

# ── /order ─────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["order"])
def cmd_order(message):
    chat_id = str(message.from_user.id)
    ok, member = is_active_member(chat_id)
    if not ok:
        if not member:
            bot.send_message(chat_id,
                "⚠️ Kamu belum terdaftar.\nKetik /daftar untuk mendaftar sebagai member.",
                parse_mode="HTML")
        elif member["status"] == "pending":
            bot.send_message(chat_id,
                "⏳ <b>Akunmu belum diaktifkan</b>\n\nAdmin sedang mereview. Tunggu notifikasi ya!",
                parse_mode="HTML")
        else:
            bot.send_message(chat_id, "❌ Akunmu tidak aktif. Hubungi admin.")
        return

    gate_msg = _check_order_gate(member)
    if gate_msg:
        bot.send_message(chat_id, gate_msg, parse_mode="HTML")
        return

    # Reset & init order state
    order_state[chat_id] = {"member": member}
    touch_order(chat_id)

    # User yang sudah konsisten pilih manual → skip tawaran Strava
    if member.get("prefers_manual"):
        _send_manual_activity_picker(chat_id)
        return

    # ── Strava terhubung ──────────────────────────────────────────────────────
    if member.get("strava_connected") and member.get("strava_refresh_token"):
        loading = bot.send_message(chat_id, "⏳ Mengambil aktivitas dari Strava...")
        token = get_strava_token(member["strava_refresh_token"])

        if token:
            raw_list = fetch_recent_activities(token, hours=24)
            berat_kg = get_berat(member["id"])

            if raw_list:
                activities = [process_strava_activity(a, berat_kg) for a in raw_list[:5]]
                order_state[chat_id]["strava_activities"] = activities

                # Satu aktivitas dan tipenya unknown → tanya dulu
                if len(activities) == 1 and activities[0]["jenis"] == "unknown":
                    order_state[chat_id]["pending_unknown_idx"] = 0
                    try: bot.delete_message(chat_id, loading.message_id)
                    except Exception: pass
                    _ask_unknown_type(chat_id, activities[0])
                    return

                msg, markup = build_activity_selection(activities)
                try:
                    bot.edit_message_text(msg, chat_id=chat_id,
                        message_id=loading.message_id, parse_mode="HTML", reply_markup=markup)
                except Exception:
                    bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
                return

            # Tidak ada aktivitas 24 jam terakhir → coba fallback aktivitas terakhir
            fallback = fetch_fallback_activity(token)
            if fallback:
                activities = [process_strava_activity(fallback[0], berat_kg)]
                order_state[chat_id]["strava_activities"] = activities
                a   = activities[0]
                em  = SPORT_EMOJI.get(a["jenis"], "❓")
                ts  = _fmt_activity_time(a["start_time"])
                dist = f"{a['jarak_km']} km · " if a["jarak_km"] > 0 else ""
                kal  = f"~{a['kalori_est']} kkal" if a["kalori_est"] > 0 else "tipe tidak dikenal"
                fallback_msg = (
                    f"⚠️ <b>Tidak ada aktivitas dalam 24 jam terakhir.</b>\n\n"
                    f"Aktivitas terakhirmu:\n"
                    f"{em} <b>{html.escape(a['nama'])}</b> · {ts}\n"
                    f"{dist}{a['waktu_menit']} mnt · {kal}\n\n"
                    f"Pakai ini atau input manual?"
                )
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("✅ Pakai aktivitas terakhir", callback_data="ord_pick:0"))
                markup.add(InlineKeyboardButton("✏️ Input Manual",             callback_data="ord_manual"))
                try:
                    bot.edit_message_text(fallback_msg, chat_id=chat_id,
                        message_id=loading.message_id, parse_mode="HTML", reply_markup=markup)
                except Exception:
                    bot.send_message(chat_id, fallback_msg, parse_mode="HTML", reply_markup=markup)
                return

        # Token gagal / tidak ada aktivitas sama sekali
        try: bot.delete_message(chat_id, loading.message_id)
        except Exception: pass
        bot.send_message(chat_id, "⚠️ Gagal mengambil data Strava. Silakan input manual.")
        _send_manual_activity_picker(chat_id)
        return

    # ── Strava belum terhubung ────────────────────────────────────────────────
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔗 Hubungkan Strava", callback_data="ord_connect_strava"))
    markup.add(InlineKeyboardButton("✏️ Input Manual",      callback_data="ord_manual"))
    bot.send_message(chat_id,
        "📍 <b>Dari mana data aktivitasmu?</b>\n\n"
        "Hubungkan Strava agar bot otomatis membaca aktivitasmu setelah olahraga.\n"
        "Atau langsung input manual sekarang.",
        parse_mode="HTML", reply_markup=markup)

def step_order_jarak(message):
    chat_id = str(message.from_user.id)
    try:
        jarak = float(message.text.strip().replace(",", "."))
        if jarak <= 0: raise ValueError
        order_state[chat_id]["jarak_km"] = jarak
        jenis = order_state[chat_id].get("jenis", "")
        emoji = {"lari": "🏃", "sepeda": "🚴", "renang": "🏊"}.get(jenis, "🏃")
        msg = bot.send_message(chat_id,
            f"{emoji} <b>Durasi</b>\n\n"
            f"Berapa lama kamu {jenis}?\n"
            f"Masukkan dalam menit. Contoh: <code>36</code>",
            parse_mode="HTML")
        bot.register_next_step_handler(msg, step_order_durasi)
    except ValueError:
        msg = bot.send_message(chat_id,
            "❌ Jarak tidak valid. Masukkan angka dalam km.\nContoh: <code>5.2</code>",
            parse_mode="HTML")
        bot.register_next_step_handler(msg, step_order_jarak)

def step_order_durasi(message):
    chat_id = str(message.from_user.id)
    try:
        durasi = float(message.text.strip().replace(",", "."))
        if durasi <= 0: raise ValueError
        order_state[chat_id]["waktu_menit"] = durasi
        ok, member = is_active_member(chat_id)
        if ok:
            bot.send_message(chat_id, "⏳ Menghitung rekomendasi menumu...")
            _kirim_menu(chat_id, member, order_state[chat_id])
    except ValueError:
        msg = bot.send_message(chat_id,
            "❌ Durasi tidak valid. Masukkan angka dalam menit.\nContoh: <code>36</code>",
            parse_mode="HTML")
        bot.register_next_step_handler(msg, step_order_durasi)

def step_order_durasi_gym(message):
    chat_id = str(message.from_user.id)
    try:
        durasi = float(message.text.strip().replace(",", "."))
        if durasi <= 0: raise ValueError
        order_state[chat_id].update({"waktu_menit": durasi, "jarak_km": 0})
        ok, member = is_active_member(chat_id)
        if ok:
            bot.send_message(chat_id, "⏳ Menghitung rekomendasi menumu...")
            _kirim_menu(chat_id, member, order_state[chat_id])
    except ValueError:
        msg = bot.send_message(chat_id,
            "❌ Durasi tidak valid. Masukkan angka dalam menit.\nContoh: <code>60</code>",
            parse_mode="HTML")
        bot.register_next_step_handler(msg, step_order_durasi_gym)

def _kirim_menu(chat_id: str, member: dict, activity: dict):
    try:
        keputusan = analyze(activity, member)
    except (LookupError, ValueError) as e:
        bot.send_message(chat_id, f"❌ {html.escape(str(e))}", parse_mode="HTML")
        return
    member_sessions[chat_id] = keputusan
    bot.send_message(
        chat_id,
        build_options_message(keputusan, member["nama"]),
        reply_markup=build_options_markup(keputusan),
        parse_mode="HTML"
    )

def _kirim_menu_by_kalori(chat_id: str, member: dict, kalori_terbakar: int, aktivitas_str: str):
    """Generate menu dari total kalori langsung (untuk mode gabung semua sesi)."""
    goal_type, modifier = get_goal_info(member["id"])
    kalori_target = round(kalori_terbakar * modifier)
    try:
        daftar_menu = _fetch_daily_menus()
    except (LookupError, ValueError) as e:
        bot.send_message(chat_id, f"❌ {html.escape(str(e))}", parse_mode="HTML")
        return

    rekomendasi = _build_rekomendasi(daftar_menu, kalori_target, goal_type)

    keputusan = {
        "aktivitas":       aktivitas_str,
        "rekomendasi":     rekomendasi,
        "berat_kg":        get_berat(member["id"]),
        "goal_type":       goal_type,
        "kalori_terbakar": kalori_terbakar,
        "kalori_target":   kalori_target,
    }
    member_sessions[chat_id] = keputusan
    bot.send_message(
        chat_id,
        build_options_message(keputusan, member["nama"]),
        reply_markup=build_options_markup(keputusan),
        parse_mode="HTML"
    )

# ── /profil ────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["profil"])
def cmd_profil(message):
    chat_id = str(message.from_user.id)
    member  = get_member(chat_id)
    if not member:
        bot.send_message(chat_id, "⚠️ Belum terdaftar. Ketik /daftar.")
        return

    m    = get_measurement(member["id"])
    g    = get_goal(member["id"])
    usia = calculate_age(member["tanggal_lahir"])

    berat  = f"{m['berat_kg']} kg" if m and m.get("berat_kg") else "-"
    tinggi = f"{m['tinggi_cm']} cm" if m and m.get("tinggi_cm") else "-"
    tgl_up = f" <i>({m['recorded_at'][:10]})</i>" if m else ""

    if g:
        pct  = round((float(g["modifier"]) - 1) * 100)
        sign = f"+{pct}" if pct >= 0 else str(pct)
        goal_text = f"{g['goal_type']} ({sign}%)"
    else:
        goal_text = "maintenance (default)"

    strava = "✅ Terhubung" if member.get("strava_connected") else "❌ Belum (/hubungkan_strava)"

    bot.send_message(chat_id,
        f"👤 <b>{html.escape(member['nama'])}</b>, {usia} tahun ({member['jenis_kelamin']})\n"
        f"⚖️ Berat: <b>{berat}</b>{tgl_up}\n"
        f"📏 Tinggi: <b>{tinggi}</b>\n"
        f"🎯 Goal: <b>{goal_text}</b>\n"
        f"🔗 Strava: {strava}",
        parse_mode="HTML")

# ── /berat /tinggi /goal ───────────────────────────────────────────────────────

@bot.message_handler(commands=["berat"])
def cmd_berat(message):
    chat_id = str(message.from_user.id)
    ok, member = is_active_member(chat_id)
    if not ok:
        return
    try:
        berat = float(message.text.split()[1].replace(",", "."))
        if not (30 <= berat <= 200): raise ValueError
        prev = get_measurement(member["id"])
        supabase.table("user_measurements").insert({
            "user_id":   member["id"],
            "berat_kg":  berat,
            "tinggi_cm": prev["tinggi_cm"] if prev else None,
            "sumber":    "telegram_command",
        }).execute()
        bot.send_message(chat_id, f"✅ Berat diupdate: <b>{berat} kg</b>", parse_mode="HTML")
    except (ValueError, IndexError):
        bot.send_message(chat_id, "❌ Format: <code>/berat 72</code>", parse_mode="HTML")

@bot.message_handler(commands=["tinggi"])
def cmd_tinggi(message):
    chat_id = str(message.from_user.id)
    ok, member = is_active_member(chat_id)
    if not ok:
        return
    try:
        tinggi = float(message.text.split()[1].replace(",", "."))
        if not (100 <= tinggi <= 250): raise ValueError
        prev = get_measurement(member["id"])
        supabase.table("user_measurements").insert({
            "user_id":   member["id"],
            "berat_kg":  prev["berat_kg"] if prev else None,
            "tinggi_cm": tinggi,
            "sumber":    "telegram_command",
        }).execute()
        bot.send_message(chat_id, f"✅ Tinggi diupdate: <b>{tinggi} cm</b>", parse_mode="HTML")
    except (ValueError, IndexError):
        bot.send_message(chat_id, "❌ Format: <code>/tinggi 173</code>", parse_mode="HTML")

@bot.message_handler(commands=["goal"])
def cmd_goal(message):
    chat_id = str(message.from_user.id)
    ok, member = is_active_member(chat_id)
    if not ok:
        return
    try:
        parts = message.text.split()
        if len(parts) < 2: raise ValueError
        goal_map = {
            "maintenance": ("maintenance", 1.0),
            "defisit": ("deficit", 0.8), "deficit": ("deficit", 0.8),
            "surplus": ("surplus", 1.2),
        }
        if parts[1].lower() not in goal_map: raise ValueError
        goal_type, modifier = goal_map[parts[1].lower()]
        if len(parts) >= 3:
            pct = float(parts[2])
            if goal_type == "deficit":  modifier = round(1 - pct / 100, 2)
            elif goal_type == "surplus": modifier = round(1 + pct / 100, 2)

        supabase.table("user_goals").insert({
            "user_id":   member["id"],
            "goal_type": goal_type,
            "modifier":  modifier,
            "aktif_dari": datetime.datetime.now().isoformat(),
        }).execute()

        pct_disp = round((modifier - 1) * 100)
        sign = f"+{pct_disp}" if pct_disp >= 0 else str(pct_disp)
        bot.send_message(chat_id,
            f"✅ Goal diupdate: <b>{goal_type}</b> ({sign}% kalori)", parse_mode="HTML")
    except (ValueError, IndexError):
        bot.send_message(chat_id,
            "❌ Format:\n<code>/goal maintenance</code>\n<code>/goal defisit</code>\n"
            "<code>/goal surplus</code>\n<code>/goal defisit 25</code> (custom %)",
            parse_mode="HTML")

# ── CALLBACKS ──────────────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = str(call.from_user.id)
    action, _, param = call.data.partition(":")

    # ── Shortcut inline buttons (Mode C) ──────────────────────────────────────
    if action == "shortcut_order":
        bot.answer_callback_query(call.id)
        call.message.from_user = call.from_user
        cmd_order(call.message)
        return
    if action == "shortcut_profil":
        bot.answer_callback_query(call.id)
        call.message.from_user = call.from_user
        cmd_profil(call.message)
        return
    if action == "shortcut_strava":
        bot.answer_callback_query(call.id)
        call.message.from_user = call.from_user
        cmd_hubungkan_strava(call.message)
        return

    # ── Registrasi: tombol "Daftar Sekarang" dari /start ──────────────────────
    if action in ("reg_cta", "reg_start"):
        if get_member(chat_id):
            bot.answer_callback_query(call.id, "Kamu sudah terdaftar.")
            return
        daftar_state[chat_id] = {"_step": "nama"}
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.send_message(chat_id,
            f"<b>{_step_bar(1)}</b>\n\n"
            "👤 <b>Siapa namamu?</b>\n\n"
            "Nama ini akan muncul di setiap ordermu di dapur.",
            parse_mode="HTML")
        return

    # ── Registrasi: pilih gender (fallback inline lama) ────────────────────────
    if action == "reg_gender":
        if chat_id not in daftar_state:
            bot.answer_callback_query(call.id, "Sesi habis. Ketik /daftar ulang.")
            return
        if param not in ("L", "P"):
            bot.answer_callback_query(call.id, "Pilihan tidak valid.")
            return
        daftar_state[chat_id]["jenis_kelamin"] = param
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        _prompt_daftar_berat(chat_id)
        return

    # ── Registrasi: pilih goal (fallback inline lama) ──────────────────────────
    if action == "reg_goal":
        if chat_id not in daftar_state:
            bot.answer_callback_query(call.id, "Sesi habis. Ketik /daftar ulang.")
            return
        if param not in GOAL_OPTIONS:
            bot.answer_callback_query(call.id, "Pilihan tidak valid.")
            return
        goal_type, modifier, _ = GOAL_OPTIONS[param]
        daftar_state[chat_id].update({"goal_type": goal_type, "modifier": modifier})
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        _show_daftar_ringkasan(chat_id)
        return

    # ── Registrasi: konfirmasi akhir ───────────────────────────────────────────
    if action == "reg_confirm":
        if param == "no":
            daftar_state.pop(chat_id, None)
            bot.answer_callback_query(call.id, "Oke, mulai dari awal.")
            try:
                bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            except Exception:
                pass
            daftar_state[chat_id] = {}
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🚀 Mulai Lagi", callback_data="reg_start"))
            bot.send_message(chat_id, "🔄 Oke, kita mulai dari awal ya!", reply_markup=markup)
        else:
            _simpan_member(call, chat_id)
        return

    # ── Order: hubungkan Strava dari /order ────────────────────────────────────
    if action == "ord_connect_strava":
        ok, member = is_active_member(chat_id)
        if not ok:
            bot.answer_callback_query(call.id, "Akses ditolak.")
            return
        bot.answer_callback_query(call.id)
        auth_url = f"{BASE_URL}/strava/auth/{chat_id}"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔗 Buka Halaman Strava", url=auth_url))
        bot.send_message(chat_id,
            "🔗 <b>Hubungkan Akun Strava</b>\n\n"
            "1. Tap tombol di bawah\n"
            "2. Login & izinkan akses Strava\n"
            "3. Kamu dapat notifikasi setelah terhubung\n"
            "4. Ketik /order lagi untuk pesan\n\n"
            "<i>⚠️ Link hanya berfungsi jika server aktif (ngrok berjalan).</i>",
            parse_mode="HTML", reply_markup=markup)
        return

    # ── Order: pilih manual (dari tawaran Strava) ──────────────────────────────
    if action == "ord_manual":
        ok, member = is_active_member(chat_id)
        if not ok:
            bot.answer_callback_query(call.id, "Akses ditolak.")
            return
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        if not is_order_fresh(chat_id):
            order_state[chat_id] = {"member": member}
            touch_order(chat_id)
        _track_manual_pref(chat_id, member["id"])
        _send_manual_activity_picker(chat_id)
        return

    # ── Order: pilih satu aktivitas dari Strava ────────────────────────────────
    if action == "ord_pick":
        ok, member = is_active_member(chat_id)
        if not ok or not is_order_fresh(chat_id):
            bot.answer_callback_query(call.id, "Sesi habis. Ketik /order lagi.")
            return
        idx = int(param) if param.isdigit() else 0
        activities = order_state[chat_id].get("strava_activities", [])
        if idx >= len(activities):
            bot.answer_callback_query(call.id, "Aktivitas tidak valid.")
            return
        a = activities[idx]
        if a["jenis"] == "unknown":
            order_state[chat_id]["pending_unknown_idx"] = idx
            bot.answer_callback_query(call.id)
            try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            except Exception: pass
            _ask_unknown_type(chat_id, a)
            return
        bot.answer_callback_query(call.id, "✅ Aktivitas dipilih")
        try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception: pass
        touch_order(chat_id)
        order_state[chat_id].update({"jenis": a["jenis"], "jarak_km": a["jarak_km"], "waktu_menit": a["waktu_menit"]})
        bot.send_message(chat_id, "⏳ Menghitung rekomendasi menumu...")
        _kirim_menu(chat_id, member, order_state[chat_id])
        return

    # ── Order: gabung semua sesi — tampil warning harga dulu ──────────────────
    if action == "ord_combine":
        ok, member = is_active_member(chat_id)
        if not ok or not is_order_fresh(chat_id):
            bot.answer_callback_query(call.id, "Sesi habis. Ketik /order lagi.")
            return
        activities  = order_state[chat_id].get("strava_activities", [])
        total_kal   = sum(a["kalori_est"] for a in activities if a["jenis"] != "unknown")
        std_kal     = 400
        std_price   = 55000
        est_price   = max(30000, round(std_price * (total_kal / std_kal) / 5000) * 5000)
        order_state[chat_id]["pending_combine_kal"] = total_kal
        bot.answer_callback_query(call.id)
        try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception: pass
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("✅ Lanjut",           callback_data="ord_combine_confirm"),
            InlineKeyboardButton("↩️ Pilih satu sesi",  callback_data="ord_combine_back"),
        )
        bot.send_message(chat_id,
            f"🔥 <b>Gabung Semua Sesi</b>\n\n"
            f"Total kalori terbakar: <b>~{total_kal} kkal</b>\n"
            f"Estimasi harga menu  : <b>~Rp {est_price:,}</b>\n\n"
            f"Porsi akan disesuaikan dengan total energi yang kamu keluarkan.\n"
            f"Lanjutkan?",
            parse_mode="HTML", reply_markup=markup)
        return

    # ── Order: konfirmasi gabung semua ─────────────────────────────────────────
    if action == "ord_combine_confirm":
        ok, member = is_active_member(chat_id)
        if not ok or not is_order_fresh(chat_id):
            bot.answer_callback_query(call.id, "Sesi habis. Ketik /order lagi.")
            return
        total_kal = order_state[chat_id].get("pending_combine_kal", 0)
        if not total_kal:
            bot.answer_callback_query(call.id, "Data tidak valid.")
            return
        bot.answer_callback_query(call.id, "✅ Menghitung menu...")
        try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception: pass
        touch_order(chat_id)
        bot.send_message(chat_id, "⏳ Menghitung rekomendasi dari total semua sesi...")
        _kirim_menu_by_kalori(chat_id, member, total_kal, "Gabungan semua sesi hari ini")
        return

    # ── Order: balik ke pilih satu sesi ───────────────────────────────────────
    if action == "ord_combine_back":
        ok, member = is_active_member(chat_id)
        if not ok or not is_order_fresh(chat_id):
            bot.answer_callback_query(call.id, "Sesi habis. Ketik /order lagi.")
            return
        bot.answer_callback_query(call.id)
        try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception: pass
        activities = order_state[chat_id].get("strava_activities", [])
        msg, markup = build_activity_selection(activities)
        bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
        return

    # ── Order: reklasifikasi tipe aktivitas unknown ────────────────────────────
    if action == "ord_retype":
        ok, member = is_active_member(chat_id)
        if not ok or not is_order_fresh(chat_id):
            bot.answer_callback_query(call.id, "Sesi habis. Ketik /order lagi.")
            return
        jenis      = param
        activities = order_state[chat_id].get("strava_activities", [])
        pending    = order_state[chat_id].get("pending_unknown_idx")
        if pending is not None and pending < len(activities):
            berat_kg = get_berat(member["id"])
            activities[pending]["jenis"] = jenis
            activities[pending]["kalori_est"] = estimate_calories({
                "jenis": jenis,
                "jarak_km":    activities[pending]["jarak_km"],
                "waktu_menit": activities[pending]["waktu_menit"],
            }, berat_kg)
        bot.answer_callback_query(call.id, f"✅ Dikategorikan sebagai {jenis}")
        try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception: pass
        touch_order(chat_id)
        if len(activities) == 1:
            a = activities[0]
            order_state[chat_id].update({"jenis": a["jenis"], "jarak_km": a["jarak_km"], "waktu_menit": a["waktu_menit"]})
            bot.send_message(chat_id, "⏳ Menghitung rekomendasi menumu...")
            _kirim_menu(chat_id, member, order_state[chat_id])
        else:
            msg, markup = build_activity_selection(activities)
            bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
        return

    # ── Order: pilih jenis aktivitas (manual) ──────────────────────────────────
    if action == "ord_activity":
        ok, member = is_active_member(chat_id)
        if not ok:
            bot.answer_callback_query(call.id, "Akses ditolak.")
            return
        jenis = param
        if not is_order_fresh(chat_id):
            order_state[chat_id] = {"member": member}
        # ord_activity sekarang ditangani lewat ReplyKeyboard + step_order_activity_picker
        bot.answer_callback_query(call.id, "Gunakan tombol keyboard di bawah.")
        return

    # ── Menu: detail / order / back ────────────────────────────────────────────
    ok, member = is_active_member(chat_id)
    if not ok:
        bot.answer_callback_query(call.id, "Akses ditolak.")
        return

    current = member_sessions.get(chat_id)
    if not current:
        bot.answer_callback_query(call.id, "Sesi habis. Ketik /order untuk mulai lagi.")
        return

    if action == "back":
        bot.answer_callback_query(call.id, "Balik ke daftar menu.")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=build_options_message(current, member["nama"]),
            reply_markup=build_options_markup(current),
            parse_mode="HTML"
        )
        return

    if action == "soldout":
        bot.answer_callback_query(call.id, "Menu ini sold out hari ini.", show_alert=True)
        return

    if action not in {"detail", "order"} or not param.isdigit():
        bot.answer_callback_query(call.id, "Pilihan tidak dikenal.")
        return

    idx = int(param)
    if idx >= len(current.get("rekomendasi", [])):
        bot.answer_callback_query(call.id, "Menu tidak valid.")
        return

    menu = current["rekomendasi"][idx]

    if action == "detail":
        bot.answer_callback_query(call.id, f"Detail {menu['nama_menu']}")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=build_detail_message(current["aktivitas"], menu),
            reply_markup=build_detail_markup(idx, menu),
            parse_mode="HTML"
        )
        return

    if menu.get("is_sold_out"):
        bot.answer_callback_query(call.id, "Menu ini sold out hari ini.", show_alert=True)
        return

    slot = resolve_delivery(now_wita())
    success = _submit_order(chat_id, member, current, menu)
    bot.answer_callback_query(call.id, "✅ Order dikirim!" if success else "❌ Gagal.")
    detail_text = build_detail_message(current["aktivitas"], menu, is_ordered=success)
    if success:
        detail_text += f"\n\nPengiriman: <b>{slot} WITA</b>"
    _finish_order_message(chat_id, call, detail_text)
    member_sessions.pop(chat_id, None)

# ── MAIN ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("Sassyroll Bot Server dimulai...")
    print(f"  Admin Chat ID : {ADMIN_CHAT_ID}")
    print(f"  Base URL      : {BASE_URL}")
    print("  Ketik /daftar di Telegram untuk mendaftar sebagai member.")
    bot.infinity_polling()
