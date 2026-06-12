import os
import requests
import datetime
import html
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv(override=True)

def required_env(name):
    value = os.getenv(name)
    if not value or not value.strip():
        raise RuntimeError(f"Variabel .env '{name}' belum diisi atau tidak terbaca.")
    return value.strip()

CLIENT_ID         = required_env("STRAVA_CLIENT_ID")
CLIENT_SECRET     = required_env("STRAVA_CLIENT_SECRET")
REFRESH_TOKEN     = required_env("STRAVA_REFRESH_TOKEN")
TELEGRAM_BOT_TOKEN = required_env("TELEGRAM_BOT_TOKEN")
CHAT_ID           = required_env("TELEGRAM_CHAT_ID")
INTERNAL_API_KEY  = required_env("INTERNAL_API_KEY")
SUPABASE_URL      = required_env("SUPABASE_URL")
SUPABASE_SERVICE_KEY = required_env("SUPABASE_SERVICE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# ── MENU DATABASE ──────────────────────────────────────────────────────────────

MENU_MINGGUAN = {
    "Friday": [
        {
            "nama": "Grilled Chicken Quinoa Bowl",
            "porsi_std": 350,
            "kalori_std": 400,
            "harga_std": 50000,
            "detail_item_std": [
                {"nama_item": "Dada ayam panggang",    "porsi_gram": 110, "kalori": 181, "protein_gram": 34,  "karbo_gram": 0,    "lemak_gram": 4},
                {"nama_item": "Quinoa putih masak",    "porsi_gram": 140, "kalori": 168, "protein_gram": 5.6, "karbo_gram": 29.4, "lemak_gram": 2.7},
                {"nama_item": "Brokoli wortel panggang","porsi_gram": 80,  "kalori": 36,  "protein_gram": 2.4, "karbo_gram": 7.2,  "lemak_gram": 0.4},
                {"nama_item": "Saus yogurt lemon",     "porsi_gram": 20,  "kalori": 15,  "protein_gram": 1,   "karbo_gram": 2,    "lemak_gram": 1}
            ]
        },
        {
            "nama": "Salmon Soba Noodles",
            "porsi_std": 300,
            "kalori_std": 450,
            "harga_std": 70000,
            "detail_item_std": [
                {"nama_item": "Salmon panggang",  "porsi_gram": 100, "kalori": 206, "protein_gram": 22,  "karbo_gram": 0,  "lemak_gram": 12},
                {"nama_item": "Soba matang",      "porsi_gram": 140, "kalori": 155, "protein_gram": 7,   "karbo_gram": 30, "lemak_gram": 0.5},
                {"nama_item": "Edamame rebus",    "porsi_gram": 40,  "kalori": 49,  "protein_gram": 4.4, "karbo_gram": 3.6,"lemak_gram": 2},
                {"nama_item": "Saus miso wijen",  "porsi_gram": 20,  "kalori": 40,  "protein_gram": 1,   "karbo_gram": 4,  "lemak_gram": 2.5}
            ]
        },
        {
            "nama": "Tempeh Miso Salad",
            "porsi_std": 400,
            "kalori_std": 300,
            "harga_std": 40000,
            "detail_item_std": [
                {"nama_item": "Tempeh panggang",      "porsi_gram": 80,  "kalori": 152, "protein_gram": 15.2,"karbo_gram": 7.2,"lemak_gram": 8},
                {"nama_item": "Miso salad greens",    "porsi_gram": 180, "kalori": 45,  "protein_gram": 4,   "karbo_gram": 8,  "lemak_gram": 1},
                {"nama_item": "Ubi kukus",             "porsi_gram": 110, "kalori": 95,  "protein_gram": 2,   "karbo_gram": 22, "lemak_gram": 0.1},
                {"nama_item": "Dressing miso ringan",  "porsi_gram": 30,  "kalori": 8,   "protein_gram": 0,   "karbo_gram": 0,  "lemak_gram": 0}
            ]
        }
    ]
}

# ── SUPABASE: USER PROFILE HELPERS ────────────────────────────────────────────

def get_user_profile() -> dict | None:
    result = supabase.table("user_profile").select("*").limit(1).execute()
    return result.data[0] if result.data else None

def get_latest_measurement() -> dict | None:
    result = supabase.table("user_measurements").select("*").order("recorded_at", desc=True).limit(1).execute()
    return result.data[0] if result.data else None

def get_active_goal() -> dict | None:
    result = supabase.table("user_goals").select("*").order("aktif_dari", desc=True).limit(1).execute()
    return result.data[0] if result.data else None

def get_user_berat() -> float:
    m = get_latest_measurement()
    return float(m["berat_kg"]) if m and m.get("berat_kg") else 65.0

def get_goal_info() -> tuple[str, float]:
    g = get_active_goal()
    if not g:
        return "maintenance", 1.0
    return g["goal_type"], float(g["modifier"])

def calculate_age(tanggal_lahir_str: str) -> int:
    tgl = datetime.date.fromisoformat(tanggal_lahir_str)
    today = datetime.date.today()
    return today.year - tgl.year - ((today.month, today.day) < (tgl.month, tgl.day))

# ── STRAVA ─────────────────────────────────────────────────────────────────────

def get_strava_access_token():
    response = requests.post("https://www.strava.com/oauth/token", data={
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'refresh_token': REFRESH_TOKEN,
        'grant_type': 'refresh_token'
    })
    return response.json().get('access_token') if response.status_code == 200 else None

def fetch_latest_activity(access_token):
    response = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={'Authorization': f'Bearer {access_token}'},
        params={'per_page': 1}
    )
    if response.status_code != 200:
        return None
    activities = response.json()
    if not activities:
        return None
    activity = activities[0]
    return {
        "nama": activity['name'],
        "jarak_km": activity['distance'] / 1000,
        "elevasi_m": activity['total_elevation_gain'],
        "waktu_menit": activity['moving_time'] / 60
    }

# ── KALORI & MENU CALCULATION ──────────────────────────────────────────────────

def estimate_calories_burned(activity_data, berat_kg: float) -> int:
    # Formula lari standar: 1.036 kkal per kg per km
    return round(activity_data['jarak_km'] * berat_kg * 1.036)

def round_macro(value):
    return round(value, 1)

def scale_menu_item(item, multiplier):
    return {
        "nama_item":    item["nama_item"],
        "porsi_gram":   round(item["porsi_gram"] * multiplier),
        "kalori":       round(item["kalori"] * multiplier),
        "protein_gram": round_macro(item["protein_gram"] * multiplier),
        "karbo_gram":   round_macro(item["karbo_gram"] * multiplier),
        "lemak_gram":   round_macro(item["lemak_gram"] * multiplier),
    }

def calculate_total_nutrition(detail_items):
    return {
        "kalori":       sum(item["kalori"] for item in detail_items),
        "protein_gram": round_macro(sum(item["protein_gram"] for item in detail_items)),
        "karbo_gram":   round_macro(sum(item["karbo_gram"] for item in detail_items)),
        "lemak_gram":   round_macro(sum(item["lemak_gram"] for item in detail_items)),
    }

def format_goal_effect(goal_type: str, modifier: float) -> str:
    if goal_type == "maintenance":
        return "ganti 100% kalori terbakar"
    if goal_type == "deficit":
        return f"defisit — ganti {round(modifier * 100)}% kalori terbakar"
    if goal_type == "surplus":
        return f"surplus — ganti {round(modifier * 100)}% kalori terbakar"
    return goal_type


def build_price_note(kalori_terbakar, kalori_target, goal_type="maintenance", modifier=1.0):
    goal_text = format_goal_effect(goal_type, modifier)
    if kalori_terbakar <= 0:
        return f"Target recovery {kalori_target} kkal. Goal: {goal_text}."
    pct_recovery = round(kalori_target / kalori_terbakar * 100)
    return (
        f"Target recovery {kalori_target} kkal "
        f"({pct_recovery}% dari {kalori_terbakar} kkal terbakar). "
        f"Goal: {goal_text}."
    )

def analyze_nutrition_and_pricing(activity_data):
    berat_kg = get_user_berat()
    goal_type, modifier = get_goal_info()

    hari_ini = datetime.datetime.now().strftime("%A")
    daftar_menu = MENU_MINGGUAN.get(hari_ini, MENU_MINGGUAN["Friday"])

    kalori_terbakar = estimate_calories_burned(activity_data, berat_kg)
    kalori_target   = round(kalori_terbakar * modifier)

    print(f"\n🧮 Berat: {berat_kg}kg | Goal: {goal_type} (×{modifier})")
    print(f"   Kalori terbakar: {kalori_terbakar} kkal → Target makan: {kalori_target} kkal")

    rekomendasi = []
    for menu in daftar_menu:
        multiplier   = kalori_target / menu["kalori_std"]
        detail_items = [scale_menu_item(item, multiplier) for item in menu["detail_item_std"]]
        total_nutrisi = calculate_total_nutrition(detail_items)
        porsi_gram    = sum(item["porsi_gram"] for item in detail_items)

        rekomendasi.append({
            "nama_menu":       menu["nama"],
            "porsi_gram":      porsi_gram,
            "protein_gram":    total_nutrisi["protein_gram"],
            "karbo_gram":      total_nutrisi["karbo_gram"],
            "harga_final":     round(menu["harga_std"] * multiplier),
            "keterangan_harga": build_price_note(
                kalori_terbakar, kalori_target, goal_type, modifier,
            ),
            "detail_item":     detail_items,
            "total_nutrisi":   total_nutrisi
        })

    return {
        "aktivitas":        f"Lari {activity_data['jarak_km']:.2f} km | Durasi {activity_data['waktu_menit']:.2f} min | Est. {kalori_terbakar} kkal terbakar",
        "rekomendasi":      rekomendasi,
        "berat_kg":         berat_kg,
        "goal_type":        goal_type,
        "kalori_terbakar":  kalori_terbakar,
        "kalori_target":    kalori_target,
    }

# ── FASTAPI ORDER ──────────────────────────────────────────────────────────────

def execute_autonomous_order(payload_final):
    print(f"\n🛒 Mengirim pesanan '{payload_final['menu_pilihan']['nama_menu']}' ke dapur...")
    try:
        response = requests.post(
            "http://127.0.0.1:8000/api/order",
            json=payload_final,
            headers={"X-API-Key": INTERNAL_API_KEY}
        )
        if response.status_code == 200:
            print("✅ INVOICE SUKSES DICETAK DI DAPUR!")
        elif response.status_code == 403:
            print("❌ Akses ditolak: INTERNAL_API_KEY tidak valid.")
        else:
            print("❌ Dapur nolak format. Error:", response.text)
    except Exception as e:
        print("❌ Dapur tutup! Error:", e)

# ── TELEGRAM: MESSAGE BUILDERS ─────────────────────────────────────────────────

def format_number(value, decimals=0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if decimals == 0:
        return f"{int(round(number)):,}".replace(",", ".")
    return f"{number:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")

def format_rupiah(value):
    return f"Rp {format_number(value)}"

def get_total_nutrisi(menu):
    total = menu.get("total_nutrisi") or {}
    if total:
        return total
    detail_items = menu.get("detail_item") or []
    return {
        "kalori":       sum(item.get("kalori", 0) for item in detail_items),
        "protein_gram": sum(item.get("protein_gram", 0) for item in detail_items),
        "karbo_gram":   sum(item.get("karbo_gram", 0) for item in detail_items),
        "lemak_gram":   sum(item.get("lemak_gram", 0) for item in detail_items),
    }

def build_options_message(keputusan):
    goal_type   = keputusan.get("goal_type", "maintenance")
    berat_kg    = keputusan.get("berat_kg", 65)
    kalori_terbakar = keputusan.get("kalori_terbakar", 0)
    kalori_target   = keputusan.get("kalori_target", 0)

    goal_emoji = {"maintenance": "⚖️", "deficit": "📉", "surplus": "📈"}.get(goal_type, "⚖️")
    modifier_pct = round((kalori_target / kalori_terbakar - 1) * 100) if kalori_terbakar else 0
    sign = f"+{modifier_pct}" if modifier_pct > 0 else str(modifier_pct)
    goal_line = f"{goal_emoji} <b>{goal_type}</b> ({sign}%) → target <b>{format_number(kalori_target)} kkal</b>"

    pesan = (
        f"🎯 <b>OPSI RECOVERY LO</b>\n"
        f"{html.escape(keputusan['aktivitas'])}\n"
        f"⚖️ Berat: {berat_kg}kg | {goal_line}\n\n"
    )
    for i, menu in enumerate(keputusan['rekomendasi']):
        total = get_total_nutrisi(menu)
        pesan += f"<b>{i+1}. {html.escape(menu['nama_menu'])}</b> ({format_number(menu['porsi_gram'])}g - {format_number(total.get('kalori'))}kkal)\n"
        pesan += f"🥩 Protein {format_number(total.get('protein_gram'), 1)}g\n"
        pesan += f"🍚 Karbo {format_number(total.get('karbo_gram'), 1)}g\n"
        pesan += f"{format_rupiah(menu['harga_final'])}\n\n"

    pesan += "Tap tombol di bawah untuk lihat detail breakdown."
    return pesan

def build_options_markup(keputusan):
    markup = InlineKeyboardMarkup()
    for i, menu in enumerate(keputusan['rekomendasi']):
        markup.add(InlineKeyboardButton(f"Lihat Detail {i+1}: {menu['nama_menu']}", callback_data=f"detail:{i}"))
    return markup

def build_detail_markup(idx):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Order menu ini", callback_data=f"order:{idx}"))
    markup.add(InlineKeyboardButton("⬅️ Lihat menu lain", callback_data="back"))
    return markup

def build_menu_detail_message(activity, menu, is_ordered=False):
    total  = get_total_nutrisi(menu)
    status = "✅ <b>ORDER DIKONFIRMASI</b>" if is_ordered else "🔎 <b>DETAIL MENU</b>"
    pesan  = f"{status}\n{html.escape(menu['nama_menu'])}\n\n"
    pesan += f"{html.escape(activity)}\n"
    pesan += f"💰 <b>Harga:</b> {format_rupiah(menu['harga_final'])}\n"
    pesan += f"📝 {html.escape(menu.get('keterangan_harga', ''))}\n\n"
    pesan += "🍽️ <b>Breakdown item & nutrisi</b>\n"

    for item in (menu.get("detail_item") or []):
        pesan += (
            f"• {html.escape(item.get('nama_item', 'Item'))} "
            f"({format_number(item.get('porsi_gram'))}g)\n"
            f"  🔥 {format_number(item.get('kalori'))} kkal\n"
            f"  🥩 {format_number(item.get('protein_gram'), 1)}g protein\n"
            f"  🍚 {format_number(item.get('karbo_gram'), 1)}g karbo\n"
            f"  🧈 {format_number(item.get('lemak_gram'), 1)}g lemak\n\n"
        )

    pesan += (
        f"📊 <b>Total</b>\n"
        f"🔥 {format_number(total.get('kalori'))} kkal\n"
        f"🥩 {format_number(total.get('protein_gram'), 1)}g protein\n"
        f"🍚 {format_number(total.get('karbo_gram'), 1)}g karbo\n"
        f"🧈 {format_number(total.get('lemak_gram'), 1)}g lemak\n\n"
    )
    pesan += "Pesanan sudah dikirim ke dapur Sassyroll." if is_ordered else "Kalau sudah cocok, tekan tombol order di bawah."
    return pesan

# ── TELEGRAM BOT SETUP ─────────────────────────────────────────────────────────

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
current_payload = {}
setup_state     = {}

# ── COMMAND: /setup ────────────────────────────────────────────────────────────

@bot.message_handler(commands=['setup'])
def cmd_setup(message):
    if str(message.chat.id) != CHAT_ID:
        return
    setup_state.clear()
    msg = bot.send_message(CHAT_ID, "⚙️ <b>Setup Profil</b>\n\nSiapa namamu?", parse_mode="HTML")
    bot.register_next_step_handler(msg, step_setup_nama)

def step_setup_nama(message):
    setup_state["nama"] = message.text.strip()
    msg = bot.send_message(CHAT_ID, "📅 Tanggal lahir? (format: DD/MM/YYYY)")
    bot.register_next_step_handler(msg, step_setup_tgl_lahir)

def step_setup_tgl_lahir(message):
    try:
        tgl = datetime.datetime.strptime(message.text.strip(), "%d/%m/%Y").date().isoformat()
        setup_state["tanggal_lahir"] = tgl
        msg = bot.send_message(CHAT_ID, "⚧ Jenis kelamin? (L / P)")
        bot.register_next_step_handler(msg, step_setup_gender)
    except ValueError:
        msg = bot.send_message(CHAT_ID, "❌ Format salah. Coba lagi: DD/MM/YYYY (contoh: 15/08/1997)")
        bot.register_next_step_handler(msg, step_setup_tgl_lahir)

def step_setup_gender(message):
    gender = message.text.strip().upper()
    if gender not in ("L", "P"):
        msg = bot.send_message(CHAT_ID, "❌ Ketik L (laki-laki) atau P (perempuan).")
        bot.register_next_step_handler(msg, step_setup_gender)
        return
    setup_state["jenis_kelamin"] = gender
    msg = bot.send_message(CHAT_ID, "⚖️ Berat badan sekarang? (kg, contoh: 70)")
    bot.register_next_step_handler(msg, step_setup_berat)

def step_setup_berat(message):
    try:
        berat = float(message.text.strip().replace(",", "."))
        if not (30 <= berat <= 200):
            raise ValueError
        setup_state["berat_kg"] = berat
        msg = bot.send_message(CHAT_ID, "📏 Tinggi badan sekarang? (cm, contoh: 172)")
        bot.register_next_step_handler(msg, step_setup_tinggi)
    except ValueError:
        msg = bot.send_message(CHAT_ID, "❌ Berat tidak valid. Masukkan angka antara 30–200 kg.")
        bot.register_next_step_handler(msg, step_setup_berat)

def step_setup_tinggi(message):
    try:
        tinggi = float(message.text.strip().replace(",", "."))
        if not (100 <= tinggi <= 250):
            raise ValueError
        setup_state["tinggi_cm"] = tinggi
        msg = bot.send_message(
            CHAT_ID,
            "🎯 Goal sekarang?\n\n"
            "1️⃣ maintenance — ganti semua kalori yang terbakar\n"
            "2️⃣ defisit — makan lebih sedikit (fat loss)\n"
            "3️⃣ surplus — makan lebih banyak (mass gain)\n\n"
            "Ketik 1, 2, atau 3."
        )
        bot.register_next_step_handler(msg, step_setup_goal)
    except ValueError:
        msg = bot.send_message(CHAT_ID, "❌ Tinggi tidak valid. Masukkan angka antara 100–250 cm.")
        bot.register_next_step_handler(msg, step_setup_tinggi)

def step_setup_goal(message):
    goal_map = {
        "1": ("maintenance", 1.0), "maintenance": ("maintenance", 1.0),
        "2": ("deficit", 0.8),     "defisit": ("deficit", 0.8), "deficit": ("deficit", 0.8),
        "3": ("surplus", 1.2),     "surplus": ("surplus", 1.2),
    }
    goal_info = goal_map.get(message.text.strip().lower())
    if not goal_info:
        msg = bot.send_message(CHAT_ID, "❌ Pilih 1, 2, atau 3.")
        bot.register_next_step_handler(msg, step_setup_goal)
        return

    goal_type, modifier = goal_info
    try:
        existing = supabase.table("user_profile").select("id").limit(1).execute()
        if existing.data:
            supabase.table("user_profile").update({
                "nama": setup_state["nama"],
                "tanggal_lahir": setup_state["tanggal_lahir"],
                "jenis_kelamin": setup_state["jenis_kelamin"],
            }).eq("id", existing.data[0]["id"]).execute()
        else:
            supabase.table("user_profile").insert({
                "nama": setup_state["nama"],
                "tanggal_lahir": setup_state["tanggal_lahir"],
                "jenis_kelamin": setup_state["jenis_kelamin"],
            }).execute()

        supabase.table("user_measurements").insert({
            "berat_kg": setup_state["berat_kg"],
            "tinggi_cm": setup_state["tinggi_cm"],
            "sumber": "telegram_setup",
        }).execute()

        supabase.table("user_goals").insert({
            "goal_type": goal_type,
            "modifier": modifier,
            "aktif_dari": datetime.datetime.now().isoformat(),
        }).execute()

        usia = calculate_age(setup_state["tanggal_lahir"])
        modifier_pct = round((modifier - 1) * 100)
        sign = f"+{modifier_pct}" if modifier_pct >= 0 else str(modifier_pct)
        bot.send_message(
            CHAT_ID,
            f"✅ <b>Profil tersimpan!</b>\n\n"
            f"👤 {html.escape(setup_state['nama'])}, {usia} tahun ({setup_state['jenis_kelamin']})\n"
            f"⚖️ Berat: {setup_state['berat_kg']}kg | 📏 Tinggi: {setup_state['tinggi_cm']}cm\n"
            f"🎯 Goal: {goal_type} ({sign}% kalori)\n\n"
            f"Sekarang jalankan ulang agent.py setelah olahraga untuk dapat rekomendasi menu yang personal!",
            parse_mode="HTML"
        )
        setup_state.clear()
    except Exception as e:
        bot.send_message(CHAT_ID, f"❌ Gagal simpan profil: {e}")

# ── COMMAND: /profil ───────────────────────────────────────────────────────────

@bot.message_handler(commands=['profil'])
def cmd_profil(message):
    if str(message.chat.id) != CHAT_ID:
        return
    profil      = get_user_profile()
    measurement = get_latest_measurement()
    goal        = get_active_goal()

    if not profil:
        bot.send_message(CHAT_ID, "⚠️ Profil belum dibuat. Ketik /setup untuk mulai.")
        return

    usia       = calculate_age(profil["tanggal_lahir"])
    berat_text = f"{measurement['berat_kg']} kg" if measurement and measurement.get("berat_kg") else "belum diisi"
    tinggi_text= f"{measurement['tinggi_cm']} cm" if measurement and measurement.get("tinggi_cm") else "belum diisi"
    tgl_update = f" <i>(update: {measurement['recorded_at'][:10]})</i>" if measurement else ""

    if goal:
        modifier_pct = round((float(goal["modifier"]) - 1) * 100)
        sign = f"+{modifier_pct}" if modifier_pct >= 0 else str(modifier_pct)
        goal_text = f"{goal['goal_type']} ({sign}%)"
    else:
        goal_text = "maintenance (default)"

    bot.send_message(
        CHAT_ID,
        f"👤 <b>{html.escape(profil['nama'])}</b>, {usia} tahun ({profil['jenis_kelamin']})\n"
        f"⚖️ Berat terkini: <b>{berat_text}</b>{tgl_update}\n"
        f"📏 Tinggi terkini: <b>{tinggi_text}</b>\n"
        f"🎯 Goal aktif: <b>{goal_text}</b>\n\n"
        f"Update dengan: /berat /tinggi /goal",
        parse_mode="HTML"
    )

# ── COMMAND: /berat ────────────────────────────────────────────────────────────

@bot.message_handler(commands=['berat'])
def cmd_berat(message):
    if str(message.chat.id) != CHAT_ID:
        return
    try:
        parts = message.text.split()
        if len(parts) < 2:
            raise ValueError
        berat = float(parts[1].replace(",", "."))
        if not (30 <= berat <= 200):
            raise ValueError

        prev = get_latest_measurement()
        supabase.table("user_measurements").insert({
            "berat_kg": berat,
            "tinggi_cm": prev["tinggi_cm"] if prev else None,
            "sumber": "telegram_command",
        }).execute()
        bot.send_message(CHAT_ID, f"✅ Berat diupdate: <b>{berat} kg</b>", parse_mode="HTML")
    except (ValueError, IndexError):
        bot.send_message(CHAT_ID, "❌ Format: <code>/berat 72</code>", parse_mode="HTML")

# ── COMMAND: /tinggi ───────────────────────────────────────────────────────────

@bot.message_handler(commands=['tinggi'])
def cmd_tinggi(message):
    if str(message.chat.id) != CHAT_ID:
        return
    try:
        parts = message.text.split()
        if len(parts) < 2:
            raise ValueError
        tinggi = float(parts[1].replace(",", "."))
        if not (100 <= tinggi <= 250):
            raise ValueError

        prev = get_latest_measurement()
        supabase.table("user_measurements").insert({
            "berat_kg": prev["berat_kg"] if prev else None,
            "tinggi_cm": tinggi,
            "sumber": "telegram_command",
        }).execute()
        bot.send_message(CHAT_ID, f"✅ Tinggi diupdate: <b>{tinggi} cm</b>", parse_mode="HTML")
    except (ValueError, IndexError):
        bot.send_message(CHAT_ID, "❌ Format: <code>/tinggi 173</code>", parse_mode="HTML")

# ── COMMAND: /goal ─────────────────────────────────────────────────────────────

@bot.message_handler(commands=['goal'])
def cmd_goal(message):
    if str(message.chat.id) != CHAT_ID:
        return
    try:
        parts = message.text.split()
        if len(parts) < 2:
            raise ValueError

        goal_map = {
            "maintenance": ("maintenance", 1.0),
            "defisit":     ("deficit",     0.8),
            "deficit":     ("deficit",     0.8),
            "surplus":     ("surplus",     1.2),
        }
        goal_input = parts[1].lower()
        if goal_input not in goal_map:
            raise ValueError

        goal_type, modifier = goal_map[goal_input]

        if len(parts) >= 3:
            custom_pct = float(parts[2])
            if goal_type == "deficit":
                modifier = round(1 - custom_pct / 100, 2)
            elif goal_type == "surplus":
                modifier = round(1 + custom_pct / 100, 2)

        supabase.table("user_goals").insert({
            "goal_type": goal_type,
            "modifier":  modifier,
            "aktif_dari": datetime.datetime.now().isoformat(),
        }).execute()

        modifier_pct = round((modifier - 1) * 100)
        sign = f"+{modifier_pct}" if modifier_pct >= 0 else str(modifier_pct)
        bot.send_message(
            CHAT_ID,
            f"✅ Goal diupdate: <b>{goal_type}</b> ({sign}% kalori)",
            parse_mode="HTML"
        )
    except (ValueError, IndexError):
        bot.send_message(
            CHAT_ID,
            "❌ Format:\n"
            "<code>/goal maintenance</code>\n"
            "<code>/goal defisit</code>\n"
            "<code>/goal surplus</code>\n"
            "<code>/goal defisit 25</code> (custom %)",
            parse_mode="HTML"
        )

# ── COMMAND: /help ─────────────────────────────────────────────────────────────

@bot.message_handler(commands=['help', 'start'])
def cmd_help(message):
    if str(message.chat.id) != CHAT_ID:
        return
    bot.send_message(
        CHAT_ID,
        "🍣 <b>Sassyroll AI Agent</b>\n\n"
        "<b>Profil</b>\n"
        "/setup — setup profil pertama kali\n"
        "/profil — lihat profil & goal saat ini\n"
        "/berat 72 — update berat badan\n"
        "/tinggi 173 — update tinggi badan\n"
        "/goal maintenance | defisit | surplus\n"
        "/goal defisit 25 — defisit custom 25%\n\n"
        "<b>Cara pakai</b>\n"
        "Jalankan <code>python agent.py</code> setelah olahraga untuk dapat rekomendasi menu otomatis.",
        parse_mode="HTML"
    )

# ── CALLBACK: menu selection ───────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda call: str(call.from_user.id) != CHAT_ID)
def handle_unauthorized(call):
    bot.answer_callback_query(call.id, "Akses ditolak.")

@bot.callback_query_handler(func=lambda call: str(call.from_user.id) == CHAT_ID)
def handle_pilihan_menu(call):
    if not current_payload:
        bot.answer_callback_query(call.id, "Sesi habis. Jalankan ulang agent.py.")
        return

    action, _, raw_idx = call.data.partition(":")

    if action == "back":
        bot.answer_callback_query(call.id, "Balik ke daftar menu.")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=build_options_message(current_payload),
            reply_markup=build_options_markup(current_payload),
            parse_mode="HTML"
        )
        return

    if action not in {"detail", "order"} or not raw_idx.isdigit():
        bot.answer_callback_query(call.id, "Pilihan tidak dikenal.")
        return

    idx = int(raw_idx)
    if idx >= len(current_payload.get("rekomendasi", [])):
        bot.answer_callback_query(call.id, "Menu tidak valid.")
        return

    menu_terpilih = current_payload['rekomendasi'][idx]

    if action == "detail":
        bot.answer_callback_query(call.id, f"Detail {menu_terpilih['nama_menu']}")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=build_menu_detail_message(current_payload['aktivitas'], menu_terpilih),
            reply_markup=build_detail_markup(idx),
            parse_mode="HTML"
        )
        return

    execute_autonomous_order({"aktivitas": current_payload['aktivitas'], "menu_pilihan": menu_terpilih})
    bot.answer_callback_query(call.id, f"{menu_terpilih['nama_menu']} dipilih.")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=build_menu_detail_message(current_payload['aktivitas'], menu_terpilih, is_ordered=True),
        parse_mode="HTML"
    )

# ── MAIN ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🤖 Agent Catering Pintar Dimulai...")
    print("   Ketik /help di Telegram untuk daftar command.")

    profil = get_user_profile()
    if not profil:
        print("⚠️  Profil belum ada — kirim /setup di Telegram untuk mulai.")

    token = get_strava_access_token()
    if token:
        latest_run = fetch_latest_activity(token)
        if latest_run:
            keputusan_catering = analyze_nutrition_and_pricing(latest_run)
            current_payload.update(keputusan_catering)

            print("📱 Ngirim opsi menu ke HP lo...")
            bot.send_message(
                CHAT_ID,
                build_options_message(keputusan_catering),
                reply_markup=build_options_markup(keputusan_catering),
                parse_mode="HTML"
            )
            print("⏳ Nunggu lo ngeklik tombol di Telegram...")
        else:
            print("⚠️  Tidak ada aktivitas terbaru di Strava.")
    else:
        print("❌ Gagal ambil token Strava.")

    print("🟢 Bot aktif — command /profil /berat /tinggi /goal siap dipakai.")
    bot.infinity_polling()
