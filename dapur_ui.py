import streamlit as st
import json
import os
import html

st.set_page_config(
    page_title="Sassyroll Kitchen Dashboard", 
    page_icon="🍣", 
    layout="wide"
)

DB_FILE = "orders_db.json"
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

def fetch_orders():
    if not os.path.exists(DB_FILE):
        return []
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

def save_orders(orders):
    with open(DB_FILE, "w") as f:
        json.dump(orders, f, indent=4)

def generate_order_id(orders):
    max_number = 0
    for order in orders:
        raw_id = str(order.get("order_id", ""))
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

def detail_items_to_text(detail_items):
    lines = []
    for item in detail_items:
        lines.append(
            " | ".join([
                str(item.get("nama_item", "")),
                str(item.get("porsi_gram", 0)),
                str(item.get("kalori", 0)),
                str(item.get("protein_gram", 0)),
                str(item.get("karbo_gram", 0)),
                str(item.get("lemak_gram", 0)),
            ])
        )
    return "\n".join(lines)

def create_order(orders, data):
    new_order = {
        "order_id": generate_order_id(orders),
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
    orders.append(new_order)
    save_orders(orders)

def update_order(orders, order_id, updates):
    for idx, order in enumerate(orders):
        if order.get("order_id") == order_id:
            orders[idx] = {**order, **updates}
            save_orders(orders)
            return True
    return False

def delete_order(orders, order_id):
    new_orders = [order for order in orders if order.get("order_id") != order_id]
    save_orders(new_orders)
    return len(new_orders) != len(orders)

def handle_status_change(order_id):
    orders = fetch_orders()
    new_status = st.session_state[f"status_{order_id}"]
    update_order(orders, order_id, {"status": new_status})

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

def render_order_card(order, orders):
    order = normalize_order(order)
    total = order["total_nutrisi"]
    
    with st.container(border=True):
        header_cols = st.columns([2, 1])
        header_cols[0].subheader(f"#{order['order_id']}")
        with header_cols[1]:
            render_status_badge(order["status"])
        
        st.markdown(f"**Menu:** {order['nama_menu']}")
        st.caption(f"🏃 Aktivitas User: {order['aktivitas']}")
        
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
            delete_order(orders, order["order_id"])
            st.success("Order dihapus.")
            st.rerun()

def render_create_form(orders):
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
                create_order(orders, {
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

st.title("🍣 Sassyroll Smart Kitchen Monitor")
st.divider()

orders = [normalize_order(order) for order in fetch_orders()]
render_create_form(orders)

toolbar_cols = st.columns([1, 1, 3])
if toolbar_cols[0].button("Refresh Data", use_container_width=True):
    st.rerun()

selected_status = toolbar_cols[1].selectbox("Filter status", ["Semua"] + STATUS_OPTIONS)
visible_orders = orders
if selected_status != "Semua":
    visible_orders = [order for order in orders if order.get("status") == selected_status]

if not visible_orders:
    st.info("🛌 Belum ada orderan masuk. Dapur masih aman terkendali!")
else:
    cols = st.columns(3)
    for idx, order in enumerate(visible_orders):
        col = cols[idx % 3]
        with col:
            render_order_card(order, orders)