from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import os
import json
from dotenv import load_dotenv

load_dotenv()
STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")

app = FastAPI(title="Sassyroll Healthy Catering Backend", version="3.0")

DB_FILE = "orders_db.json"

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

# Kasir sekarang cuma nerima SATU menu pilihan
class ConfirmedOrder(BaseModel):
    aktivitas: str
    menu_pilihan: MenuOption

def load_orders():
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
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

def model_to_dict(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()

@app.post("/api/order")
def terima_pesanan_otomatis(order: ConfirmedOrder):
    menu = order.menu_pilihan
    
    # 1. Baca database lama
    db = load_orders()
    total_nutrisi = menu.total_nutrisi or NutritionTotal(
        protein_gram=menu.protein_gram,
        karbo_gram=menu.karbo_gram
    )
        
    # 2. Format orderan baru
    new_order = {
        "order_id": generate_order_id(db),
        "status": "Baru",
        "aktivitas": order.aktivitas,
        "nama_menu": menu.nama_menu,
        "porsi_gram": menu.porsi_gram,
        "protein_gram": total_nutrisi.protein_gram,
        "karbo_gram": total_nutrisi.karbo_gram,
        "lemak_gram": total_nutrisi.lemak_gram,
        "kalori": total_nutrisi.kalori,
        "harga_final": menu.harga_final,
        "keterangan_harga": menu.keterangan_harga,
        "detail_item": [model_to_dict(item) for item in menu.detail_item],
        "total_nutrisi": model_to_dict(total_nutrisi)
    }
    
    # 3. Tulis ke database buat dibaca Streamlit
    db.append(new_order)
    save_orders(db)
        
    print(f"✅ Pesanan {menu.nama_menu} sukses masuk ke orders_db.json!")
    
    return {"status": "success", "message": "Pesanan fix masuk ke layar dapur."}

@app.get("/api/orders")
def list_orders():
    return load_orders()

@app.put("/api/order/{order_id}")
def update_order(order_id: str, updates: dict):
    db = load_orders()
    for idx, existing_order in enumerate(db):
        if existing_order.get("order_id") == order_id:
            db[idx] = {**existing_order, **updates, "order_id": order_id}
            save_orders(db)
            return {"status": "success", "message": f"Order {order_id} berhasil diupdate.", "order": db[idx]}
    raise HTTPException(status_code=404, detail=f"Order {order_id} tidak ditemukan.")

@app.delete("/api/order/{order_id}")
def delete_order(order_id: str):
    db = load_orders()
    new_db = [order for order in db if order.get("order_id") != order_id]
    if len(new_db) == len(db):
        raise HTTPException(status_code=404, detail=f"Order {order_id} tidak ditemukan.")
    
    save_orders(new_db)
    return {"status": "success", "message": f"Order {order_id} berhasil dihapus."}