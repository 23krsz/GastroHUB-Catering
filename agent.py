import os
import requests
import datetime
import html
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv(override=True)
CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")

# --- KONEKSI DATABASE ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def required_env(name):
    value = os.getenv(name)
    if not value or not value.strip():
        raise RuntimeError(f"Variabel .env '{name}' belum diisi atau tidak terbaca.")
    return value.strip()

CLIENT_ID = required_env("STRAVA_CLIENT_ID")
CLIENT_SECRET = required_env("STRAVA_CLIENT_SECRET")
REFRESH_TOKEN = required_env("STRAVA_REFRESH_TOKEN")
TELEGRAM_BOT_TOKEN = required_env("TELEGRAM_BOT_TOKEN")
CHAT_ID = required_env("TELEGRAM_CHAT_ID")

# DATABASE MENU MINGGUAN (Bisa diganti ngambil dari file JSON eksternal nanti)
MENU_MINGGUAN = {
    "Friday": [
        {
            "nama": "Grilled Chicken Quinoa Bowl",
            "porsi_std": 350,
            "kalori_std": 400,
            "harga_std": 50000,
            "detail_item_std": [
                {"nama_item": "Dada ayam panggang", "porsi_gram": 110, "kalori": 181, "protein_gram": 34, "karbo_gram": 0, "lemak_gram": 4},
                {"nama_item": "Quinoa putih masak", "porsi_gram": 140, "kalori": 168, "protein_gram": 5.6, "karbo_gram": 29.4, "lemak_gram": 2.7},
                {"nama_item": "Brokoli wortel panggang", "porsi_gram": 80, "kalori": 36, "protein_gram": 2.4, "karbo_gram": 7.2, "lemak_gram": 0.4},
                {"nama_item": "Saus yogurt lemon", "porsi_gram": 20, "kalori": 15, "protein_gram": 1, "karbo_gram": 2, "lemak_gram": 1}
            ]
        },
        {
            "nama": "Salmon Soba Noodles",
            "porsi_std": 300,
            "kalori_std": 450,
            "harga_std": 70000,
            "detail_item_std": [
                {"nama_item": "Salmon panggang", "porsi_gram": 100, "kalori": 206, "protein_gram": 22, "karbo_gram": 0, "lemak_gram": 12},
                {"nama_item": "Soba matang", "porsi_gram": 140, "kalori": 155, "protein_gram": 7, "karbo_gram": 30, "lemak_gram": 0.5},
                {"nama_item": "Edamame rebus", "porsi_gram": 40, "kalori": 49, "protein_gram": 4.4, "karbo_gram": 3.6, "lemak_gram": 2},
                {"nama_item": "Saus miso wijen", "porsi_gram": 20, "kalori": 40, "protein_gram": 1, "karbo_gram": 4, "lemak_gram": 2.5}
            ]
        },
        {
            "nama": "Tempeh Miso Salad",
            "porsi_std": 400,
            "kalori_std": 300,
            "harga_std": 40000,
            "detail_item_std": [
                {"nama_item": "Tempeh panggang", "porsi_gram": 80, "kalori": 152, "protein_gram": 15.2, "karbo_gram": 7.2, "lemak_gram": 8},
                {"nama_item": "Miso salad greens", "porsi_gram": 180, "kalori": 45, "protein_gram": 4, "karbo_gram": 8, "lemak_gram": 1},
                {"nama_item": "Ubi kukus", "porsi_gram": 110, "kalori": 95, "protein_gram": 2, "karbo_gram": 22, "lemak_gram": 0.1},
                {"nama_item": "Dressing miso ringan", "porsi_gram": 30, "kalori": 8, "protein_gram": 0, "karbo_gram": 0, "lemak_gram": 0}
            ]
        }
    ]
}

def get_strava_access_token():
    url = "https://www.strava.com/oauth/token"
    payload = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'refresh_token': REFRESH_TOKEN,
        'grant_type': 'refresh_token'
    }
    response = requests.post(url, data=payload)
    if response.status_code == 200:
        return response.json()['access_token']
    return None

def fetch_latest_activity(access_token):
    url = "https://www.strava.com/api/v3/athlete/activities"
    headers = {'Authorization': f'Bearer {access_token}'}
    response = requests.get(url, headers=headers, params={'per_page': 1})
    
    if response.status_code == 200 and len(response.json()) > 0:
        activity = response.json()[0]
        return {
            "nama": activity['name'],
            "jarak_km": activity['distance'] / 1000,
            "elevasi_m": activity['total_elevation_gain'],
            "waktu_menit": activity['moving_time'] / 60
        }
    return None

def estimate_calories_burned(activity_data, body_weight_kg=65):
    # Rumus lari sederhana: sekitar 1 kkal per kg berat badan per km.
    return round(activity_data['jarak_km'] * body_weight_kg)

def round_macro(value):
    return round(value, 1)

def scale_menu_item(item, multiplier):
    return {
        "nama_item": item["nama_item"],
        "porsi_gram": round(item["porsi_gram"] * multiplier),
        "kalori": round(item["kalori"] * multiplier),
        "protein_gram": round_macro(item["protein_gram"] * multiplier),
        "karbo_gram": round_macro(item["karbo_gram"] * multiplier),
        "lemak_gram": round_macro(item["lemak_gram"] * multiplier),
    }

def calculate_total_nutrition(detail_items):
    return {
        "kalori": sum(item["kalori"] for item in detail_items),
        "protein_gram": round_macro(sum(item["protein_gram"] for item in detail_items)),
        "karbo_gram": round_macro(sum(item["karbo_gram"] for item in detail_items)),
        "lemak_gram": round_macro(sum(item["lemak_gram"] for item in detail_items)),
    }

def build_price_note(multiplier):
    percentage = round((multiplier - 1) * 100, 1)
    if percentage > 0:
        return f"Penyesuaian porsi +{percentage}% dari standar untuk recovery."
    if percentage < 0:
        return f"Penyesuaian porsi {percentage}% dari standar karena target recovery lebih ringan."
    return "Porsi standar sudah sesuai target recovery."

def analyze_nutrition_and_pricing(activity_data):
    # 1. Cek Hari Ini
    hari_ini = datetime.datetime.now().strftime("%A")
    # Fallback ke Friday kalau harinya belum dibikin di database dummy
    daftar_menu = MENU_MINGGUAN.get(hari_ini, MENU_MINGGUAN["Friday"]) 
    kalori_terbakar = estimate_calories_burned(activity_data)
    
    print(f"\n🧮 Hari ini {hari_ini}. Hitung menu deterministik dari database...")
    
    rekomendasi = []
    for menu in daftar_menu:
        multiplier = kalori_terbakar / menu["kalori_std"]
        detail_items = [
            scale_menu_item(item, multiplier)
            for item in menu["detail_item_std"]
        ]
        total_nutrisi = calculate_total_nutrition(detail_items)
        porsi_gram = sum(item["porsi_gram"] for item in detail_items)
        
        rekomendasi.append({
            "nama_menu": menu["nama"],
            "porsi_gram": porsi_gram,
            "protein_gram": total_nutrisi["protein_gram"],
            "karbo_gram": total_nutrisi["karbo_gram"],
            "harga_final": round(menu["harga_std"] * multiplier),
            "keterangan_harga": build_price_note(multiplier),
            "detail_item": detail_items,
            "total_nutrisi": total_nutrisi
        })
    
    return {
        "aktivitas": f"Lari {activity_data['jarak_km']:.2f} km | Durasi {activity_data['waktu_menit']:.2f} min | Est. {kalori_terbakar} kkal terbakar",
        "rekomendasi": rekomendasi
    }

def execute_autonomous_order(payload_final):
    print(f"\n🛒 Mengirim pesanan fix '{payload_final['menu_pilihan']['nama_menu']}' ke dapur...")
    url_dapur = "http://127.0.0.1:8000/api/order"
    
    try:
        response = requests.post(url_dapur, json=payload_final)
        if response.status_code == 200:
            print("✅ INVOICE FIX SUKSES DICETAK DI DAPUR!")
        else:
            print("❌ Dapur nolak format. Error:", response.text)
    except Exception as e:
        print("❌ Dapur tutup! Error:", e)

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
        "kalori": sum(item.get("kalori", 0) for item in detail_items),
        "protein_gram": sum(item.get("protein_gram", 0) for item in detail_items),
        "karbo_gram": sum(item.get("karbo_gram", 0) for item in detail_items),
        "lemak_gram": sum(item.get("lemak_gram", 0) for item in detail_items),
    }

def build_options_message(keputusan_catering):
    pesan = f"🎯 <b>OPSI RECOVERY LO</b>\n{html.escape(keputusan_catering['aktivitas'])}\n\n"
    
    for i, menu in enumerate(keputusan_catering['rekomendasi']):
        total = get_total_nutrisi(menu)
        pesan += f"<b>{i+1}. {html.escape(menu['nama_menu'])}</b> ({format_number(menu['porsi_gram'])}g - {format_number(total.get('kalori'))}kkal)\n"
        pesan += f"🥩 Protein {format_number(total.get('protein_gram'), 1)}g\n"
        pesan += f"🍚 Karbo {format_number(total.get('karbo_gram'), 1)}g\n\n"
        pesan += f"{format_rupiah(menu['harga_final'])}\n\n"
    
    pesan += "Tap salah satu tombol di bawah buat lihat breakdown detailnya."
    return pesan

def build_options_markup(keputusan_catering):
    markup = InlineKeyboardMarkup()
    for i, menu in enumerate(keputusan_catering['rekomendasi']):
        tombol = InlineKeyboardButton(f"Lihat Detail {i+1}: {menu['nama_menu']}", callback_data=f"detail:{i}")
        markup.add(tombol)
    return markup

def build_detail_markup(idx):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Order menu ini", callback_data=f"order:{idx}"))
    markup.add(InlineKeyboardButton("⬅️ Lihat menu lain", callback_data="back"))
    return markup

def build_menu_detail_message(activity, menu, is_ordered=False):
    total = get_total_nutrisi(menu)
    status = "✅ <b>ORDER DIKONFIRMASI</b>" if is_ordered else "🔎 <b>DETAIL MENU</b>"
    pesan = f"{status}\n{html.escape(menu['nama_menu'])}\n\n"
    pesan += f"🏃 {html.escape(activity)}\n"
    pesan += f"💰 <b>Total Harga:</b> {format_rupiah(menu['harga_final'])}\n"
    pesan += f"📝 {html.escape(menu.get('keterangan_harga', ''))}\n\n"
    pesan += "<b>Breakdown item & nutrisi</b>\n"
    
    detail_items = menu.get("detail_item") or []
    if detail_items:
        for item in detail_items:
            pesan += (
                f"• <b>{html.escape(item.get('nama_item', 'Item'))}</b> "
                f"({format_number(item.get('porsi_gram'))}g)\n"
                f"  🔥 {format_number(item.get('kalori'))} kkal | "
                f"🥩 P {format_number(item.get('protein_gram'), 1)}g | "
                f"🍚 K {format_number(item.get('karbo_gram'), 1)}g | "
                f"🥑 L {format_number(item.get('lemak_gram'), 1)}g\n\n"
            )
    else:
        pesan += "Detail item belum tersedia dari AI.\n"
    
    pesan += "<b>Total nutrisi</b>\n"
    pesan += f"🔥 Kalori: {format_number(total.get('kalori'))} kkal\n"
    pesan += f"🥩 Protein: {format_number(total.get('protein_gram'), 1)}g\n"
    pesan += f"🍚 Karbo: {format_number(total.get('karbo_gram'), 1)}g\n"
    pesan += f"🥑 Lemak: {format_number(total.get('lemak_gram'), 1)}g\n\n"
    
    if is_ordered:
        pesan += "Pesanan sudah dikirim ke dapur Sassyroll."
    else:
        pesan += "Kalau sudah cocok, tekan tombol order di bawah."
    return pesan

# Inisialisasi Bot Telegram
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
current_payload = {} # Memori sementara buat nyimpen data AI

# Fungsi ini yang bakal nangkep klik tombol di HP lo
@bot.callback_query_handler(func=lambda call: True)
def handle_pilihan_menu(call):
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
    
    payload_final = {
        "aktivitas": current_payload['aktivitas'],
        "menu_pilihan": menu_terpilih
    }
    
    # Tembak API Dapur FastAPI
    execute_autonomous_order(payload_final)
    bot.answer_callback_query(call.id, f"{menu_terpilih['nama_menu']} dipilih.")
    
    # Edit pesan Telegram biar rapi (tombol ilang setelah diklik)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=build_menu_detail_message(current_payload['aktivitas'], menu_terpilih, is_ordered=True),
        parse_mode="HTML"
    )
    bot.stop_polling() # Matiin agen setelah tugas selesai

if __name__ == "__main__":
    print("🤖 Agent Catering Pintar Dimulai...")
    token = get_strava_access_token()
    
    if token:
        latest_run = fetch_latest_activity(token)
        if latest_run:
            keputusan_catering = analyze_nutrition_and_pricing(latest_run)
            current_payload = keputusan_catering
            
            print("📱 Ngirim opsi menu ke HP lo...")
            bot.send_message(
                CHAT_ID,
                build_options_message(keputusan_catering),
                reply_markup=build_options_markup(keputusan_catering),
                parse_mode="HTML"
            )
            
            print("⏳ Nunggu lo ngeklik tombol di Telegram...")
            bot.infinity_polling()