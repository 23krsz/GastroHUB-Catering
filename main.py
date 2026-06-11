from fastapi import FastAPI, HTTPException, Security
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field
import os
import requests as http_requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

def _required(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Variabel .env '{name}' belum diisi.")
    return v

INTERNAL_API_KEY     = _required("INTERNAL_API_KEY")
SUPABASE_URL         = _required("SUPABASE_URL")
SUPABASE_SERVICE_KEY = _required("SUPABASE_SERVICE_KEY")
STRAVA_CLIENT_ID     = _required("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = _required("STRAVA_CLIENT_SECRET")
TELEGRAM_BOT_TOKEN   = _required("TELEGRAM_BOT_TOKEN")
BASE_URL             = os.getenv("BASE_URL", "http://localhost:8000")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def _notify_telegram(chat_id: str, text: str):
    try:
        http_requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=5
        )
    except Exception:
        pass

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(key: str = Security(api_key_header)):
    if not key or key != INTERNAL_API_KEY:
        raise HTTPException(status_code=403, detail="Akses ditolak: API key tidak valid.")
    return key

app = FastAPI(title="Sassyroll Healthy Catering Backend", version="4.0")


class MenuItem(BaseModel):
    nama_item: str
    porsi_gram: int
    kalori: int
    protein_gram: float
    karbo_gram: float
    lemak_gram: float = 0

class NutritionTotal(BaseModel):
    kalori: int = 0
    protein_gram: float = 0
    karbo_gram: float = 0
    lemak_gram: float = 0

class MenuOption(BaseModel):
    nama_menu: str
    porsi_gram: int
    protein_gram: float
    karbo_gram: float
    harga_final: int
    keterangan_harga: str
    detail_item: list[MenuItem] = Field(default_factory=list)
    total_nutrisi: NutritionTotal | None = None

class ConfirmedOrder(BaseModel):
    aktivitas: str
    menu_pilihan: MenuOption
    member_nama: str = "Admin"
    telegram_chat_id: str | None = None
    delivery_slot: str | None = None
    delivery_date: str | None = None
    menu_template_id: int | None = None
    is_sold_out_at_order: bool = False

class OrderUpdate(BaseModel):
    status: str | None = None
    keterangan_harga: str | None = None

    def to_patch(self) -> dict:
        allowed = {"status", "keterangan_harga"}
        return {k: v for k, v in self.model_dump().items() if k in allowed and v is not None}


def generate_order_id() -> str:
    result = supabase.table("orders").select("order_id").execute()
    max_number = 0
    for row in result.data:
        raw_id = str(row.get("order_id", ""))
        if raw_id.startswith("ORD-") and raw_id[4:].isdigit():
            max_number = max(max_number, int(raw_id[4:]))
    return f"ORD-{max_number + 1:03d}"


@app.post("/api/order")
def terima_pesanan_otomatis(order: ConfirmedOrder, _: str = Security(verify_api_key)):
    menu = order.menu_pilihan
    total_nutrisi = menu.total_nutrisi or NutritionTotal(
        protein_gram=menu.protein_gram,
        karbo_gram=menu.karbo_gram,
    )
    new_order = {
        "order_id":         generate_order_id(),
        "status":           "Baru",
        "aktivitas":        order.aktivitas,
        "nama_menu":        menu.nama_menu,
        "porsi_gram":       menu.porsi_gram,
        "protein_gram":     total_nutrisi.protein_gram,
        "karbo_gram":       total_nutrisi.karbo_gram,
        "lemak_gram":       total_nutrisi.lemak_gram,
        "kalori":           total_nutrisi.kalori,
        "harga_final":      menu.harga_final,
        "keterangan_harga": menu.keterangan_harga,
        "detail_item":      [item.model_dump() for item in menu.detail_item],
        "total_nutrisi":    total_nutrisi.model_dump(),
        "member_nama":      order.member_nama,
        "telegram_chat_id": order.telegram_chat_id,
        "delivery_slot":    order.delivery_slot,
        "delivery_date":    order.delivery_date,
        "menu_template_id": order.menu_template_id,
        "is_sold_out_at_order": order.is_sold_out_at_order,
    }
    supabase.table("orders").insert(new_order).execute()
    return {"status": "success", "order_id": new_order["order_id"]}


@app.get("/api/orders")
def list_orders(_: str = Security(verify_api_key)):
    result = supabase.table("orders").select("*").order("created_at", desc=True).execute()
    return result.data


@app.put("/api/order/{order_id}")
def update_order(order_id: str, updates: OrderUpdate, _: str = Security(verify_api_key)):
    patch = updates.to_patch()
    if not patch:
        raise HTTPException(status_code=400, detail="Tidak ada field yang valid untuk diupdate.")
    result = supabase.table("orders").update(patch).eq("order_id", order_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail=f"Order {order_id} tidak ditemukan.")
    return {"status": "success", "message": f"Order {order_id} berhasil diupdate.", "order": result.data[0]}


@app.delete("/api/order/{order_id}")
def delete_order(order_id: str, _: str = Security(verify_api_key)):
    result = supabase.table("orders").delete().eq("order_id", order_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail=f"Order {order_id} tidak ditemukan.")
    return {"status": "success", "message": f"Order {order_id} berhasil dihapus."}


# ── STRAVA OAUTH ───────────────────────────────────────────────────────────────

@app.get("/strava/auth/{telegram_chat_id}")
def strava_auth(telegram_chat_id: str):
    """Redirect member ke halaman auth Strava."""
    auth_url = (
        "https://www.strava.com/oauth/authorize"
        f"?client_id={STRAVA_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={BASE_URL}/strava/callback"
        f"&scope=activity:read_all"
        f"&state={telegram_chat_id}"
        f"&approval_prompt=force"
    )
    return RedirectResponse(url=auth_url)

@app.get("/strava/callback")
def strava_callback(code: str, state: str, scope: str = ""):
    """Terima callback dari Strava, simpan refresh_token, notifikasi member."""
    telegram_chat_id = state

    # Tukar code dengan token
    r = http_requests.post("https://www.strava.com/oauth/token", data={
        "client_id":     STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code":          code,
        "grant_type":    "authorization_code",
    })
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail="Gagal tukar kode Strava.")

    refresh_token = r.json().get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Refresh token tidak ditemukan.")

    # Simpan ke Supabase
    result = supabase.table("user_profile").update({
        "strava_refresh_token": refresh_token,
        "strava_connected":     True,
    }).eq("telegram_chat_id", telegram_chat_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Member tidak ditemukan.")

    # Notifikasi member via Telegram
    member_nama = result.data[0].get("nama", "Member")
    _notify_telegram(
        telegram_chat_id,
        f"✅ <b>Strava berhasil terhubung!</b>\n\n"
        f"Halo {member_nama}! Sekarang kamu bisa ketik /order setelah olahraga dan "
        f"bot akan otomatis ambil aktivitas terakhirmu dari Strava."
    )

    return HTMLResponse(content="""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px;">
        <h2>✅ Strava Berhasil Terhubung!</h2>
        <p>Kembali ke Telegram dan ketik <b>/order</b> setelah olahraga.</p>
        </body></html>
    """)
