"""
Import master menu dari Excel ke Supabase.
Usage:
  python scripts/seed_menus_from_excel.py
  python scripts/seed_menus_from_excel.py path/to/file.xlsx
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from menu_service import invalidate_cache  # noqa: E402

CATEGORY_ORDER = {
    "karbohidrat": 1,
    "protein": 2,
    "serat": 3,
    "lemak sehat": 4,
    "cairan / elektrolit": 5,
}


def required_env(name: str) -> str:
    import os
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Env {name} belum di-set")
    return val


def load_excel(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    xl = pd.ExcelFile(path)
    menu_sheet = next(s for s in xl.sheet_names if "menu" in s.lower() and "detail" not in s.lower())
    item_sheet = next(s for s in xl.sheet_names if "detail" in s.lower())
    menus = pd.read_excel(path, sheet_name=menu_sheet).dropna(how="all")
    items = pd.read_excel(path, sheet_name=item_sheet).dropna(how="all")
    menus.columns = [str(c).strip().lower() for c in menus.columns]
    items.columns = [str(c).strip().lower() for c in items.columns]
    return menus, items


def clear_templates(supabase):
    existing = supabase.table("menu_templates").select("id").execute().data or []
    if not existing:
        return
    ids = [r["id"] for r in existing]
    supabase.table("menu_template_items").delete().in_("template_id", ids).execute()
    supabase.table("menu_templates").delete().in_("id", ids).execute()


def main(path: Path):
    menus, items = load_excel(path)
    menus["day_key"] = menus["day_key"].astype(str).str.strip().str.lower()
    items["day_key"] = items["day_key"].astype(str).str.strip().str.lower()

    supabase = create_client(required_env("SUPABASE_URL"), required_env("SUPABASE_SERVICE_KEY"))

    day_rows = supabase.table("menu_days").select("id, day_key").execute().data or []
    day_map = {d["day_key"]: d["id"] for d in day_rows}
    if len(day_map) < 7:
        raise RuntimeError(f"menu_days belum lengkap ({len(day_map)}/7). Jalankan migration dulu.")

    clear_templates(supabase)

    inserted_templates = 0
    inserted_items = 0

    for _, row in menus.iterrows():
        day_key = row["day_key"]
        if day_key not in day_map:
            raise ValueError(f"day_key tidak dikenal: {day_key}")

        tpl = supabase.table("menu_templates").insert({
            "day_id":     day_map[day_key],
            "slot":       int(row["slot"]),
            "nama":       str(row["nama_menu"]).strip(),
            "kalori_std": float(row["kalori_std"]),
            "harga_std":  float(row["harga_std"]),
            "porsi_std":  float(row["porsi_std"]) if pd.notna(row.get("porsi_std")) else None,
            "est_hpp":    float(row["est_hpp"]) if pd.notna(row.get("est_hpp")) else None,
            "is_active":  True,
        }).execute()
        template_id = tpl.data[0]["id"]
        inserted_templates += 1

        menu_items = items[
            (items["day_key"] == day_key)
            & (items["nama_menu"] == row["nama_menu"])
        ].copy()

        if len(menu_items) != 5:
            raise ValueError(
                f"{day_key} / {row['nama_menu']}: expected 5 items, got {len(menu_items)}"
            )

        menu_items["_cat_order"] = menu_items["kategori_gizi"].astype(str).str.lower().map(
            lambda c: CATEGORY_ORDER.get(c, 99)
        )
        menu_items = menu_items.sort_values("_cat_order")

        payload = []
        for order, (_, it) in enumerate(menu_items.iterrows(), start=1):
            payload.append({
                "template_id":   template_id,
                "item_order":    order,
                "item_category": str(it["kategori_gizi"]).strip(),
                "nama_item":     str(it["nama_item"]).strip(),
                "porsi_gram":    float(it["porsi_gram"]),
                "unit":          "g",
                "kalori":        float(it["kalori_kcal"]),
                "protein_gram":  float(it["protein_g"]),
                "karbo_gram":    float(it["karbohidrat_g"]),
                "lemak_gram":    float(it["lemak_g"]),
                "serat_gram":    float(it["serat_g"]) if pd.notna(it.get("serat_g")) else 0,
                "sort_order":    order,
            })
        supabase.table("menu_template_items").insert(payload).execute()
        inserted_items += len(payload)

    invalidate_cache()
    print(f"OK: {inserted_templates} templates, {inserted_items} items imported from {path.name}")


if __name__ == "__main__":
    default = ROOT / "data" / "sassyroll_menu_update.xlsx"
    excel_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    if not excel_path.exists():
        raise SystemExit(f"File tidak ditemukan: {excel_path}")
    main(excel_path)
