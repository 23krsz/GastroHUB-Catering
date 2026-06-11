import streamlit as st
import os
import html
import hashlib
import pandas as pd
import altair as alt
from datetime import datetime, timezone, date
from dotenv import load_dotenv
from supabase import create_client, Client

from menu_service import get_day_key, get_menus_for_day, get_today_date, set_sold_out, invalidate_cache as invalidate_menu_cache
from holiday_service import get_holiday_settings, save_holiday_settings, invalidate_cache as invalidate_holiday_cache

load_dotenv()

st.set_page_config(
    page_title="Sassyroll Kitchen Dashboard", 
    page_icon="🍣", 
    layout="wide"
)

# ── AUTH ──────────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return hashlib.sha256(password.strip().encode()).hexdigest()

def check_login(password: str) -> bool:
    stored_hash = os.getenv("DASHBOARD_PASSWORD_HASH", "")
    if not stored_hash:
        return False
    return hash_password(password) == stored_hash

def render_login():
    col_left, col_center, col_right = st.columns([1, 1.2, 1])
    with col_center:
        st.markdown("<br><br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("### 🍣 Sassyroll Kitchen")
            st.markdown("Masuk untuk lanjut ke dashboard.")
            with st.form("login_form"):
                password = st.text_input("Password", type="password", placeholder="Masukkan password dapur")
                submitted = st.form_submit_button("Masuk", use_container_width=True)
                if submitted:
                    if check_login(password):
                        st.session_state["authenticated"] = True
                        st.rerun()
                    else:
                        st.error("Password salah.")

if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

if not st.session_state["authenticated"]:
    render_login()
    st.stop()

# ── DASHBOARD (hanya tampil setelah login) ────────────────────────────────────

_supabase_url = os.getenv("SUPABASE_URL")
_supabase_key = os.getenv("SUPABASE_SERVICE_KEY")
if not _supabase_url or not _supabase_key:
    st.error("Variabel .env 'SUPABASE_URL' atau 'SUPABASE_SERVICE_KEY' belum diisi.")
    st.stop()

supabase: Client = create_client(_supabase_url, _supabase_key)

STATUS_OPTIONS = ["Baru", "Diproses", "Selesai", "Dibatalkan"]
STATUS_STYLES = {
    "Baru": {"bg": "#dcfce7", "color": "#166534"},
    "Diproses": {"bg": "#fef9c3", "color": "#854d0e"},
    "Selesai": {"bg": "#fee2e2", "color": "#991b1b"},
    "Dibatalkan": {"bg": "#f3f4f6", "color": "#374151"},
}
MENU_TEMPLATES = {
    "Grilled Chicken Quinoa Bowl": {
        "porsi_std": 350,
        "detail_item_std": [
            {"nama_item": "Dada ayam panggang", "porsi_gram": 110, "kalori": 181, "protein_gram": 34, "karbo_gram": 0, "lemak_gram": 4},
            {"nama_item": "Quinoa putih masak", "porsi_gram": 140, "kalori": 168, "protein_gram": 5.6, "karbo_gram": 29.4, "lemak_gram": 2.7},
            {"nama_item": "Brokoli wortel panggang", "porsi_gram": 80, "kalori": 36, "protein_gram": 2.4, "karbo_gram": 7.2, "lemak_gram": 0.4},
            {"nama_item": "Saus yogurt lemon", "porsi_gram": 20, "kalori": 15, "protein_gram": 1, "karbo_gram": 2, "lemak_gram": 1},
        ],
    },
    "Salmon Soba Noodles": {
        "porsi_std": 300,
        "detail_item_std": [
            {"nama_item": "Salmon panggang", "porsi_gram": 100, "kalori": 206, "protein_gram": 22, "karbo_gram": 0, "lemak_gram": 12},
            {"nama_item": "Soba matang", "porsi_gram": 140, "kalori": 155, "protein_gram": 7, "karbo_gram": 30, "lemak_gram": 0.5},
            {"nama_item": "Edamame rebus", "porsi_gram": 40, "kalori": 49, "protein_gram": 4.4, "karbo_gram": 3.6, "lemak_gram": 2},
            {"nama_item": "Saus miso wijen", "porsi_gram": 20, "kalori": 40, "protein_gram": 1, "karbo_gram": 4, "lemak_gram": 2.5},
        ],
    },
    "Tempeh Miso Salad": {
        "porsi_std": 400,
        "detail_item_std": [
            {"nama_item": "Tempeh panggang", "porsi_gram": 80, "kalori": 152, "protein_gram": 15.2, "karbo_gram": 7.2, "lemak_gram": 8},
            {"nama_item": "Miso salad greens", "porsi_gram": 180, "kalori": 45, "protein_gram": 4, "karbo_gram": 8, "lemak_gram": 1},
            {"nama_item": "Ubi kukus", "porsi_gram": 110, "kalori": 95, "protein_gram": 2, "karbo_gram": 22, "lemak_gram": 0.1},
            {"nama_item": "Dressing miso ringan", "porsi_gram": 30, "kalori": 8, "protein_gram": 0, "karbo_gram": 0, "lemak_gram": 0},
        ],
    },
}

def fetch_orders() -> list:
    try:
        result = (
            supabase.table("orders")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        st.error(f"Gagal mengambil data dari Supabase: {e}")
        return []

def generate_order_id() -> str:
    result = supabase.table("orders").select("order_id").execute()
    max_number = 0
    for row in result.data or []:
        raw_id = str(row.get("order_id", ""))
        if raw_id.startswith("ORD-") and raw_id[4:].isdigit():
            max_number = max(max_number, int(raw_id[4:]))
    return f"ORD-{max_number + 1:03d}"

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

def calculate_total_nutrition(detail_items):
    return {
        "kalori": sum(item.get("kalori", 0) for item in detail_items),
        "protein_gram": round(sum(item.get("protein_gram", 0) for item in detail_items), 1),
        "karbo_gram": round(sum(item.get("karbo_gram", 0) for item in detail_items), 1),
        "lemak_gram": round(sum(item.get("lemak_gram", 0) for item in detail_items), 1),
    }

def scale_menu_item(item, multiplier):
    return {
        "nama_item": item["nama_item"],
        "porsi_gram": round(item["porsi_gram"] * multiplier),
        "kalori": round(item["kalori"] * multiplier),
        "protein_gram": round(item["protein_gram"] * multiplier, 1),
        "karbo_gram": round(item["karbo_gram"] * multiplier, 1),
        "lemak_gram": round(item["lemak_gram"] * multiplier, 1),
    }

def build_fallback_detail_items(order):
    template = MENU_TEMPLATES.get(order.get("nama_menu"))
    if not template:
        return []
    
    try:
        multiplier = float(order.get("porsi_gram", 0)) / template["porsi_std"]
    except (TypeError, ValueError, ZeroDivisionError):
        return []
    
    if multiplier <= 0:
        return []
    
    return [
        scale_menu_item(item, multiplier)
        for item in template["detail_item_std"]
    ]

def normalize_order(order):
    detail_items = order.get("detail_item") or build_fallback_detail_items(order)
    total_nutrisi = order.get("total_nutrisi") or calculate_total_nutrition(detail_items)
    
    if not total_nutrisi.get("protein_gram"):
        total_nutrisi["protein_gram"] = order.get("protein_gram", 0)
    if not total_nutrisi.get("karbo_gram"):
        total_nutrisi["karbo_gram"] = order.get("karbo_gram", 0)
    if not total_nutrisi.get("kalori"):
        total_nutrisi["kalori"] = order.get("kalori", 0)
    
    return {
        **order,
        "status": order.get("status", "Baru"),
        "detail_item": detail_items,
        "total_nutrisi": total_nutrisi,
        "kalori": total_nutrisi.get("kalori", order.get("kalori", 0)),
        "protein_gram": total_nutrisi.get("protein_gram", order.get("protein_gram", 0)),
        "karbo_gram": total_nutrisi.get("karbo_gram", order.get("karbo_gram", 0)),
        "lemak_gram": total_nutrisi.get("lemak_gram", order.get("lemak_gram", 0)),
    }

def parse_detail_items(raw_text):
    detail_items = []
    for line in raw_text.splitlines():
        if not line.strip():
            continue
        
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 6:
            raise ValueError("Format detail item harus: Nama | gram | kalori | protein | karbo | lemak")
        
        detail_items.append({
            "nama_item": parts[0],
            "porsi_gram": int(float(parts[1])),
            "kalori": int(float(parts[2])),
            "protein_gram": float(parts[3]),
            "karbo_gram": float(parts[4]),
            "lemak_gram": float(parts[5]),
        })
    return detail_items

def create_order(data: dict):
    new_order = {
        "order_id": generate_order_id(),
        "status": data["status"],
        "aktivitas": data["aktivitas"],
        "nama_menu": data["nama_menu"],
        "porsi_gram": data["porsi_gram"],
        "protein_gram": data["total_nutrisi"]["protein_gram"],
        "karbo_gram": data["total_nutrisi"]["karbo_gram"],
        "lemak_gram": data["total_nutrisi"]["lemak_gram"],
        "kalori": data["total_nutrisi"]["kalori"],
        "harga_final": data["harga_final"],
        "keterangan_harga": data["keterangan_harga"],
        "detail_item": data["detail_item"],
        "total_nutrisi": data["total_nutrisi"],
    }
    supabase.table("orders").insert(new_order).execute()

def update_order(order_id: str, updates: dict) -> bool:
    result = supabase.table("orders").update(updates).eq("order_id", order_id).execute()
    return bool(result.data)

def delete_order(order_id: str) -> bool:
    result = supabase.table("orders").delete().eq("order_id", order_id).execute()
    return bool(result.data)

def handle_status_change(order_id: str):
    new_status = st.session_state[f"status_{order_id}"]
    update_order(order_id, {"status": new_status})

def render_status_badge(status):
    style = STATUS_STYLES.get(status, STATUS_STYLES["Dibatalkan"])
    st.markdown(
        (
            f"<span style='display:inline-block;padding:5px 10px;border-radius:999px;"
            f"background:{style['bg']};color:{style['color']};font-size:12px;font-weight:700;'>"
            f"{html.escape(status)}</span>"
        ),
        unsafe_allow_html=True
    )

def render_compact_summary(order, total):
    st.markdown(
        f"""
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0 14px 0;">
            <div style="font-size:12px;color:#6b7280;">⚖️ Porsi<br><b style="font-size:15px;color:#111827;">{format_number(order['porsi_gram'])}g</b></div>
            <div style="font-size:12px;color:#6b7280;">🔥 Kalori<br><b style="font-size:15px;color:#111827;">{format_number(total.get('kalori'))}</b></div>
            <div style="font-size:12px;color:#6b7280;">🥩 Pro<br><b style="font-size:15px;color:#111827;">{format_number(total.get('protein_gram'), 1)}g</b></div>
            <div style="font-size:12px;color:#6b7280;">🍚 Karbo<br><b style="font-size:15px;color:#111827;">{format_number(total.get('karbo_gram'), 1)}g</b></div>
        </div>
        """,
        unsafe_allow_html=True
    )

def render_detail_items(detail_items):
    if not detail_items:
        st.warning("Belum ada breakdown gramasi item untuk order ini.")
        return
    
    st.markdown("**Breakdown gramasi kitchen**")
    rows = ""
    for item in detail_items:
        rows += (
            "<tr>"
            f"<td style='padding:6px 2px;border-bottom:1px solid #f3f4f6;'>{html.escape(str(item.get('nama_item', 'Item')))}</td>"
            f"<td style='padding:6px 2px;text-align:left;border-bottom:1px solid #f3f4f6;font-weight:700;'>{format_number(item.get('porsi_gram'))}g</td>"
            "</tr>"
        )
    
    table_html = (
        "<table style='width:100%;border-collapse:collapse;font-size:12px;line-height:1.3;'>"
        "<thead>"
        "<tr style='color:#6b7280;border-bottom:1px solid #e5e7eb;'>"
        "<th style='text-align:center;padding:4px 2px;'>Nama</th>"
        "<th style='text-align:center;padding:4px 2px;'>Gram</th>"
        "</tr>"
        "</thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
    )
    st.markdown(table_html, unsafe_allow_html=True)

def render_order_card(order):
    order = normalize_order(order)
    total = order["total_nutrisi"]
    
    with st.container(border=True):
        header_cols = st.columns([2, 1])
        header_cols[0].subheader(f"#{order['order_id']}")
        with header_cols[1]:
            render_status_badge(order["status"])
        
        member_nama = order.get("member_nama", "Admin")
        st.markdown(f"**Menu:** {order['nama_menu']}")
        delivery = order.get("delivery_slot")
        if delivery:
            delivery_date = order.get("delivery_date", "")
            st.caption(f"🚚 Kirim {delivery} WITA · {delivery_date}")
        st.caption(f"👤 {member_nama} | 🏃 {order['aktivitas']}")
        
        render_compact_summary(order, total)
        
        st.markdown(f"**💰 {format_rupiah(order['harga_final'])}**")
        st.info(f"Catatan: {order['keterangan_harga']}")
        render_detail_items(order["detail_item"])
        
        st.selectbox(
            "Update status",
            STATUS_OPTIONS,
            index=STATUS_OPTIONS.index(order["status"]) if order["status"] in STATUS_OPTIONS else 0,
            key=f"status_{order['order_id']}",
            on_change=handle_status_change,
            args=(order["order_id"],)
        )
        
        delete_key = f"confirm_delete_{order['order_id']}"
        confirm_delete = st.checkbox("Saya yakin mau hapus order ini", key=delete_key)
        if st.button("Hapus Order", key=f"delete_{order['order_id']}", use_container_width=True, disabled=not confirm_delete):
            delete_order(order["order_id"])
            st.success("Order dihapus.")
            st.rerun()

def fetch_measurements() -> list:
    try:
        result = supabase.table("user_measurements").select("*").order("recorded_at").execute()
        return result.data or []
    except Exception:
        return []

def fetch_goals_history() -> list:
    try:
        result = supabase.table("user_goals").select("*").order("aktif_dari").execute()
        return result.data or []
    except Exception:
        return []

def fetch_user_profile() -> dict | None:
    try:
        result = supabase.table("user_profile").select("*").limit(1).execute()
        return result.data[0] if result.data else None
    except Exception:
        return None

def render_analitik():
    orders_raw = fetch_orders()
    measurements = fetch_measurements()
    goals_history = fetch_goals_history()
    profil = fetch_user_profile()

    if not orders_raw and not measurements:
        st.info("Belum ada data untuk ditampilkan. Lakukan order pertama dan setup profil dulu.")
        return

    # ── HEADER PROFIL ──────────────────────────────────────────────────────────
    if profil:
        from datetime import date
        tgl_lahir = date.fromisoformat(profil["tanggal_lahir"])
        usia = (date.today() - tgl_lahir).days // 365
        latest_m = measurements[-1] if measurements else None
        latest_g = goals_history[-1] if goals_history else None

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("👤 User", f"{profil['nama']}, {usia}th")
        col2.metric("⚖️ Berat Terkini", f"{latest_m['berat_kg']} kg" if latest_m and latest_m.get("berat_kg") else "-")
        col3.metric("📏 Tinggi Terkini", f"{latest_m['tinggi_cm']} cm" if latest_m and latest_m.get("tinggi_cm") else "-")
        col4.metric("🎯 Goal Aktif", latest_g["goal_type"].capitalize() if latest_g else "maintenance")
        st.divider()

    # ── SECTION 1: TREN BERAT BADAN ────────────────────────────────────────────
    if measurements:
        st.subheader("⚖️ Tren Berat Badan")
        df_berat = pd.DataFrame([
            {"Tanggal": m["recorded_at"][:10], "Berat (kg)": float(m["berat_kg"])}
            for m in measurements if m.get("berat_kg")
        ])
        if not df_berat.empty:
            df_berat["Tanggal"] = pd.to_datetime(df_berat["Tanggal"])
            berat_min = df_berat["Berat (kg)"].min() - 2
            berat_max = df_berat["Berat (kg)"].max() + 2

            chart_berat = alt.Chart(df_berat).mark_line(point=True, color="#6366f1").encode(
                x=alt.X("Tanggal:T", title="Tanggal", axis=alt.Axis(format="%d %b %Y")),
                y=alt.Y("Berat (kg):Q", title="Berat (kg)", scale=alt.Scale(domain=[berat_min, berat_max])),
                tooltip=[
                    alt.Tooltip("Tanggal:T", format="%d %b %Y"),
                    alt.Tooltip("Berat (kg):Q", format=".1f")
                ]
            ).properties(height=250)
            st.altair_chart(chart_berat, use_container_width=True)

            delta = df_berat["Berat (kg)"].iloc[-1] - df_berat["Berat (kg)"].iloc[0] if len(df_berat) > 1 else 0
            sign = "+" if delta > 0 else ""
            st.caption(f"Perubahan sejak pertama dicatat: **{sign}{delta:.1f} kg**")
        st.divider()

    # ── SECTION 2: RINGKASAN ORDER ─────────────────────────────────────────────
    if orders_raw:
        st.subheader("📦 Ringkasan Order")

        total_orders = len(orders_raw)
        total_pengeluaran = sum(o.get("harga_final", 0) for o in orders_raw)
        avg_kalori = sum(o.get("kalori", 0) for o in orders_raw) / total_orders if total_orders else 0

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Order", total_orders)
        col2.metric("Total Pengeluaran", f"Rp {total_pengeluaran:,}".replace(",", "."))
        col3.metric("Rata-rata Kalori/Order", f"{avg_kalori:.0f} kkal")
        st.divider()

    # ── SECTION 3: KALORI PER ORDER ────────────────────────────────────────────
    if orders_raw:
        st.subheader("🔥 Kalori per Order")
        df_orders = pd.DataFrame([
            {
                "Order": o.get("order_id", ""),
                "Kalori": int(o.get("kalori", 0)),
                "Protein (g)": float(o.get("protein_gram", 0)),
                "Karbo (g)": float(o.get("karbo_gram", 0)),
                "Lemak (g)": float(o.get("lemak_gram", 0)),
                "Menu": o.get("nama_menu", ""),
                "Tanggal": o.get("created_at", "")[:10] if o.get("created_at") else o.get("order_id", ""),
            }
            for o in orders_raw
        ])

        chart_kalori = alt.Chart(df_orders).mark_bar(color="#f97316").encode(
            x=alt.X("Order:N", title="Order ID", sort=None),
            y=alt.Y("Kalori:Q", title="Kalori (kkal)"),
            color=alt.Color("Menu:N", legend=alt.Legend(title="Menu")),
            tooltip=["Order:N", "Menu:N", "Kalori:Q", "Protein (g):Q", "Karbo (g):Q", "Lemak (g):Q"]
        ).properties(height=280)
        st.altair_chart(chart_kalori, use_container_width=True)
        st.divider()

    # ── SECTION 4: NUTRISI RATA-RATA ───────────────────────────────────────────
    if orders_raw:
        st.subheader("🥗 Rata-rata Nutrisi per Order")

        avg_protein = sum(o.get("protein_gram", 0) for o in orders_raw) / len(orders_raw)
        avg_karbo   = sum(o.get("karbo_gram", 0)   for o in orders_raw) / len(orders_raw)
        avg_lemak   = sum(o.get("lemak_gram", 0)   for o in orders_raw) / len(orders_raw)

        df_nutrisi = pd.DataFrame({
            "Makro": ["🥩 Protein", "🍚 Karbo", "🥑 Lemak"],
            "Gram": [avg_protein, avg_karbo, avg_lemak],
            "Warna": ["#22c55e", "#f59e0b", "#ef4444"],
        })

        chart_nutrisi = alt.Chart(df_nutrisi).mark_bar().encode(
            x=alt.X("Makro:N", title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("Gram:Q", title="Gram (rata-rata)"),
            color=alt.Color("Warna:N", scale=None, legend=None),
            tooltip=["Makro:N", alt.Tooltip("Gram:Q", format=".1f")]
        ).properties(height=220)
        st.altair_chart(chart_nutrisi, use_container_width=True)
        st.divider()

    # ── SECTION 5: MENU FAVORIT ────────────────────────────────────────────────
    if orders_raw:
        st.subheader("🍽️ Menu Favorit")

        menu_counts = {}
        for o in orders_raw:
            nama = o.get("nama_menu", "Unknown")
            menu_counts[nama] = menu_counts.get(nama, 0) + 1

        df_menu = pd.DataFrame([
            {"Menu": k, "Jumlah Order": v}
            for k, v in sorted(menu_counts.items(), key=lambda x: -x[1])
        ])

        chart_menu = alt.Chart(df_menu).mark_bar(color="#8b5cf6").encode(
            x=alt.X("Jumlah Order:Q", title="Jumlah Order"),
            y=alt.Y("Menu:N", sort="-x", title=None),
            tooltip=["Menu:N", "Jumlah Order:Q"]
        ).properties(height=max(100, len(df_menu) * 50))
        st.altair_chart(chart_menu, use_container_width=True)
        st.divider()

    # ── SECTION 6: RIWAYAT GOAL ────────────────────────────────────────────────
    if goals_history:
        st.subheader("🎯 Riwayat Goal")
        df_goals = pd.DataFrame([
            {
                "Tanggal": g["aktif_dari"][:10],
                "Goal": g["goal_type"].capitalize(),
                "Modifier": f"×{float(g['modifier']):.2f}",
            }
            for g in goals_history
        ])
        st.dataframe(df_goals, use_container_width=True, hide_index=True)


def _notify_telegram(chat_id: str, text: str, reply_markup: dict | None = None):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id:
        return
    try:
        import requests as _req
        payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=5
        )
    except Exception:
        pass

# Keyboard permanen yang dikirim ke member saat akun diaktifkan
_MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": "🛒 Order"}, {"text": "👤 Profil"}],
        [{"text": "🔗 Strava"}, {"text": "❓ Help"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

def fetch_all_members() -> list:
    try:
        result = supabase.table("user_profile").select("*").order("created_at", desc=True).execute()
        return result.data or []
    except Exception as e:
        st.error(f"Gagal ambil data member: {e}")
        return []

def render_members():
    st.subheader("👥 Kelola Member")

    if st.button("🔄 Refresh", key="refresh_members"):
        st.rerun()

    members = fetch_all_members()
    if not members:
        st.info("Belum ada member yang terdaftar.")
        return

    # ── PENDING ────────────────────────────────────────────────────────────────
    pending = [m for m in members if m.get("status") == "pending"]
    if pending:
        st.markdown("#### ⏳ Menunggu Persetujuan")
        for m in pending:
            with st.container(border=True):
                c1, c2, c3 = st.columns([3, 1, 1])
                tgl_lahir = m.get("tanggal_lahir", "")
                from datetime import date
                try:
                    usia = (date.today() - date.fromisoformat(tgl_lahir)).days // 365
                except Exception:
                    usia = "-"
                c1.markdown(
                    f"**{html.escape(m['nama'])}**, {usia} tahun ({m.get('jenis_kelamin','-')})\n\n"
                    f"`{m.get('telegram_chat_id', '-')}`"
                )
                if c2.button("✅ Approve", key=f"approve_{m['id']}", use_container_width=True):
                    supabase.table("user_profile").update({
                        "status":      "aktif",
                        "approved_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", m["id"]).execute()
                    _notify_telegram(
                        m.get("telegram_chat_id", ""),
                        f"✅ <b>Akunmu sudah aktif!</b>\n\n"
                        f"Halo <b>{html.escape(m['nama'])}</b>! Kamu sekarang bisa order.\n\n"
                        f"Gunakan tombol di bawah atau ketik /order setelah olahraga.",
                        reply_markup=_MAIN_KEYBOARD,
                    )
                    st.success(f"{m['nama']} disetujui.")
                    st.rerun()
                if c3.button("❌ Tolak", key=f"tolak_{m['id']}", use_container_width=True):
                    supabase.table("user_profile").update({"status": "nonaktif"}).eq("id", m["id"]).execute()
                    _notify_telegram(
                        m.get("telegram_chat_id", ""),
                        f"❌ Pendaftaranmu ditolak oleh admin. Hubungi admin untuk informasi lebih lanjut."
                    )
                    st.warning(f"{m['nama']} ditolak.")
                    st.rerun()
        st.divider()

    # ── AKTIF ──────────────────────────────────────────────────────────────────
    aktif = [m for m in members if m.get("status") == "aktif"]
    st.markdown(f"#### ✅ Member Aktif ({len(aktif)})")
    if aktif:
        for m in aktif:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                strava = "✅ Strava" if m.get("strava_connected") else "❌ Strava"
                tgl_lahir = m.get("tanggal_lahir", "")
                try:
                    from datetime import date
                    usia = (date.today() - date.fromisoformat(tgl_lahir)).days // 365
                except Exception:
                    usia = "-"
                c1.markdown(
                    f"**{html.escape(m['nama'])}**, {usia} tahun ({m.get('jenis_kelamin','-')}) | {strava}\n\n"
                    f"`{m.get('telegram_chat_id', '-')}`"
                )
                if c2.button("🚫 Nonaktifkan", key=f"deactivate_{m['id']}", use_container_width=True):
                    supabase.table("user_profile").update({"status": "nonaktif"}).eq("id", m["id"]).execute()
                    _notify_telegram(
                        m.get("telegram_chat_id", ""),
                        "❌ Akunmu telah dinonaktifkan oleh admin."
                    )
                    st.warning(f"{m['nama']} dinonaktifkan.")
                    st.rerun()
    else:
        st.info("Belum ada member aktif.")

    # ── NONAKTIF ───────────────────────────────────────────────────────────────
    nonaktif = [m for m in members if m.get("status") == "nonaktif"]
    if nonaktif:
        st.divider()
        st.markdown(f"#### 🚫 Nonaktif ({len(nonaktif)})")
        for m in nonaktif:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                c1.markdown(f"**{html.escape(m['nama'])}** | `{m.get('telegram_chat_id', '-')}`")
                if c2.button("↩️ Aktifkan", key=f"reactivate_{m['id']}", use_container_width=True):
                    supabase.table("user_profile").update({
                        "status":      "aktif",
                        "approved_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", m["id"]).execute()
                    _notify_telegram(
                        m.get("telegram_chat_id", ""),
                        "✅ <b>Akunmu telah diaktifkan kembali!</b> Ketik /help untuk mulai."
                    )
                    st.success(f"{m['nama']} diaktifkan kembali.")
                    st.rerun()


def render_operasional():
    st.subheader("⚙️ Operasional Dapur")

    col_holiday, col_soldout = st.columns(2)

    with col_holiday:
        st.markdown("#### 🏖️ Mode Libur")
        settings = get_holiday_settings(supabase)
        active = st.toggle(
            "Aktifkan mode libur",
            value=bool(settings.get("active")),
            key="holiday_active",
        )
        message = st.text_area(
            "Pesan penolakan /order",
            value=str(settings.get("message") or ""),
            height=120,
            key="holiday_message",
        )
        if st.button("💾 Simpan Mode Libur", key="save_holiday", use_container_width=True):
            save_holiday_settings(supabase, active, message)
            invalidate_holiday_cache()
            st.success("Mode libur disimpan.")
            st.rerun()

        st.caption("Saat aktif, semua /order ditolak dengan pesan di atas.")

        st.divider()
        st.markdown("**Broadcast manual**")
        broadcast_text = st.text_area(
            "Pesan ke semua member aktif",
            placeholder="Contoh: Dapur libur besok karena libur nasional.",
            height=80,
            key="broadcast_text",
        )
        if st.button("📣 Kirim Broadcast", key="send_broadcast", use_container_width=True):
            if not broadcast_text.strip():
                st.warning("Isi pesan broadcast dulu.")
            else:
                members = [
                    m for m in fetch_all_members()
                    if m.get("status") == "aktif" and m.get("telegram_chat_id")
                ]
                sent = 0
                for m in members:
                    _notify_telegram(m["telegram_chat_id"], broadcast_text.strip())
                    sent += 1
                st.success(f"Broadcast terkirim ke {sent} member.")

    with col_soldout:
        st.markdown("#### 🔴 Sold Out Hari Ini")
        today = get_today_date()
        day_key = get_day_key()
        day_labels = {
            "monday": "Senin", "tuesday": "Selasa", "wednesday": "Rabu",
            "thursday": "Kamis", "friday": "Jumat", "saturday": "Sabtu", "sunday": "Minggu",
        }
        st.caption(f"Tanggal WITA: {today.isoformat()} ({day_labels.get(day_key, day_key)})")

        try:
            menus = get_menus_for_day(supabase, day_key)
        except Exception as e:
            st.error(str(e))
            return

        with st.form("soldout_form"):
            sold_out_map = {}
            for menu in menus:
                sold_out_map[menu["id"]] = st.checkbox(
                    f"{menu['slot']}. {menu['nama']}",
                    value=bool(menu.get("is_sold_out")),
                    key=f"soldout_{menu['id']}",
                )
            if st.form_submit_button("💾 Simpan Sold Out", use_container_width=True):
                for menu in menus:
                    set_sold_out(
                        supabase,
                        menu["id"],
                        today,
                        sold_out_map[menu["id"]],
                    )
                invalidate_menu_cache(day_key)
                st.success("Status sold out diperbarui.")
                st.rerun()

        st.caption("Menu sold out tetap tampil di bot dengan label, tapi tidak bisa dipesan.")


def render_create_form():
    st.sidebar.header("Create Order Manual")
    with st.sidebar.form("create_order_form"):
        nama_menu = st.text_input("Nama menu")
        aktivitas = st.text_area("Aktivitas", value="Manual kitchen order")
        status = st.selectbox("Status", STATUS_OPTIONS)
        harga_final = st.number_input("Harga final", min_value=0, value=0)
        keterangan_harga = st.text_area(
            "Catatan penyesuaian",
            value="Penyesuaian porsi dari standar sesuai kebutuhan recovery."
        )
        detail_raw = st.text_area(
            "Detail item kitchen",
            placeholder="Dada ayam panggang | 90 | 148 | 27.7 | 0 | 3.3\nQuinoa putih masak | 114 | 137 | 4.6 | 24 | 2.2",
            help="Satu item per baris: Nama | gram | kalori | protein | karbo | lemak"
        )
        submitted = st.form_submit_button("Tambah Order")

        if submitted:
            try:
                detail_items = parse_detail_items(detail_raw)
                total_nutrisi = calculate_total_nutrition(detail_items)
                create_order({
                    "nama_menu": nama_menu,
                    "aktivitas": aktivitas,
                    "status": status,
                    "porsi_gram": sum(item["porsi_gram"] for item in detail_items),
                    "harga_final": harga_final,
                    "keterangan_harga": keterangan_harga,
                    "detail_item": detail_items,
                    "total_nutrisi": total_nutrisi,
                })
                st.success("Order manual berhasil dibuat.")
                st.rerun()
            except ValueError as error:
                st.error(str(error))

title_col, logout_col = st.columns([5, 1])
title_col.title("🍣 Sassyroll Smart Kitchen Monitor")
if logout_col.button("Logout", use_container_width=True):
    st.session_state["authenticated"] = False
    st.rerun()
st.divider()

tab_dapur, tab_operasional, tab_analitik, tab_member = st.tabs(
    ["🍳 Dapur", "⚙️ Operasional", "📊 Analitik", "👥 Members"]
)

# ── TAB DAPUR ──────────────────────────────────────────────────────────────────

with tab_dapur:
    orders = [normalize_order(order) for order in fetch_orders()]
    render_create_form()

    toolbar_cols = st.columns([1, 1, 3])
    if toolbar_cols[0].button("Refresh Data", use_container_width=True):
        st.rerun()

    selected_status = toolbar_cols[1].selectbox("Filter status", ["Semua"] + STATUS_OPTIONS)
    slot_filter = toolbar_cols[2].selectbox("Filter batch", ["Semua", "09:00", "12:00", "15:00", "18:00"])
    visible_orders = orders
    if selected_status != "Semua":
        visible_orders = [order for order in visible_orders if order.get("status") == selected_status]
    if slot_filter != "Semua":
        visible_orders = [order for order in visible_orders if order.get("delivery_slot") == slot_filter]

    if not visible_orders:
        st.info("🛌 Belum ada orderan masuk. Dapur masih aman terkendali!")
    else:
        cols = st.columns(3)
        for idx, order in enumerate(visible_orders):
            with cols[idx % 3]:
                render_order_card(order)

# ── TAB OPERASIONAL ────────────────────────────────────────────────────────────

with tab_operasional:
    render_operasional()

# ── TAB ANALITIK ───────────────────────────────────────────────────────────────

with tab_analitik:
    render_analitik()

# ── TAB MEMBERS ────────────────────────────────────────────────────────────────

with tab_member:
    render_members()