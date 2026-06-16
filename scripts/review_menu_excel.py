"""Review master menu Excel against import spec."""
import sys
from pathlib import Path

import pandas as pd

DAY_KEYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
REQUIRED_MENU = ["day_key", "slot", "nama_menu", "harga_std", "kalori_std", "porsi_std"]
REQUIRED_ITEM = [
    "day_key", "slot", "item_order", "nama_item",
    "porsi_gram", "kalori", "protein_gram", "karbo_gram", "lemak_gram",
]


def main(path: str):
    p = Path(path)
    xl = pd.ExcelFile(p)
    print("SHEETS:", xl.sheet_names)
    print()

    sheet_map = {s.lower(): s for s in xl.sheet_names}
    if "menus" not in sheet_map or "menu_items" not in sheet_map:
        print("WARNING: expected sheets 'menus' and 'menu_items'")
        menus = pd.read_excel(p, sheet_name=xl.sheet_names[0])
        items = pd.read_excel(p, sheet_name=xl.sheet_names[1]) if len(xl.sheet_names) > 1 else None
    else:
        menus = pd.read_excel(p, sheet_name=sheet_map["menus"])
        items = pd.read_excel(p, sheet_name=sheet_map["menu_items"])

    menus.columns = [str(c).strip().lower() for c in menus.columns]
    if items is not None:
        items.columns = [str(c).strip().lower() for c in items.columns]

    print("=== menus columns ===")
    print(list(menus.columns))
    print("rows:", len(menus.dropna(how="all")))
    print()
    if items is not None:
        print("=== menu_items columns ===")
        print(list(items.columns))
        print("rows:", len(items.dropna(how="all")))
        print()

    issues: list[str] = []
    menus = menus.dropna(how="all")
    if items is not None:
        items = items.dropna(how="all")

    for c in REQUIRED_MENU:
        if c not in menus.columns:
            issues.append(f"menus: missing column '{c}'")
    if items is not None:
        for c in REQUIRED_ITEM:
            if c not in items.columns:
                issues.append(f"menu_items: missing column '{c}'")

    if "day_key" in menus.columns:
        menus["day_key"] = menus["day_key"].astype(str).str.strip().str.lower()
        bad = set(menus["day_key"]) - DAY_KEYS
        if bad:
            issues.append(f"invalid day_key values: {bad}")
        print("Menus per day_key:")
        print(menus.groupby("day_key").size().sort_index().to_string())
        print()
        if menus.duplicated(["day_key", "slot"]).any():
            d = menus[menus.duplicated(["day_key", "slot"], keep=False)]
            issues.append(f"duplicate day_key+slot rows: {len(d)}")

    if len(menus) != 21:
        issues.append(f"expected 21 menu rows, got {len(menus)}")

    if "slot" in menus.columns:
        bad_slot = menus[~menus["slot"].isin([1, 2, 3])]
        if len(bad_slot):
            issues.append(f"{len(bad_slot)} rows with slot not in 1,2,3")

    for col in ["harga_std", "kalori_std", "porsi_std"]:
        if col in menus.columns:
            na = menus[col].isna().sum()
            if na:
                issues.append(f"menus: {na} empty values in {col}")

    if items is not None and "day_key" in items.columns:
        items["day_key"] = items["day_key"].astype(str).str.strip().str.lower()
        cnt = items.groupby(["day_key", "slot"]).size()
        bad_cnt = cnt[cnt != 5]
        if len(bad_cnt):
            issues.append(f"menus without exactly 5 items: {bad_cnt.to_dict()}")
        print("Items count per menu (first 10):")
        print(cnt.head(10).to_string())
        print()

        item_sum = items.groupby(["day_key", "slot"])["kalori"].sum().reset_index(name="sum_kal")
        merged = menus.merge(item_sum, on=["day_key", "slot"], how="left")
        merged["diff"] = (merged["sum_kal"] - pd.to_numeric(merged["kalori_std"], errors="coerce")).abs()
        bad_k = merged[merged["diff"] > 10]
        if len(bad_k):
            issues.append(f"{len(bad_k)} menus: |sum(item kalori) - kalori_std| > 10")
            print("Kalori mismatches (>10 kkal):")
            print(bad_k[["day_key", "slot", "nama_menu", "kalori_std", "sum_kal", "diff"]].to_string())
            print()
        else:
            print("Kalori check: OK (tolerance 10 kkal)")
            print()

        gram_sum = items.groupby(["day_key", "slot"])["porsi_gram"].sum().reset_index(name="sum_g")
        merged2 = menus.merge(gram_sum, on=["day_key", "slot"], how="left")
        merged2["diff_g"] = (merged2["sum_g"] - pd.to_numeric(merged2["porsi_std"], errors="coerce")).abs()
        bad_g = merged2[merged2["diff_g"] > 20]
        if len(bad_g):
            issues.append(f"{len(bad_g)} menus: |sum(item gram) - porsi_std| > 20g")
            print("Porsi mismatches (>20g):")
            print(bad_g[["day_key", "slot", "nama_menu", "porsi_std", "sum_g", "diff_g"]].to_string())
            print()
        else:
            print("Porsi check: OK (tolerance 20g)")
            print()

    print("=" * 50)
    print(f"ISSUES: {len(issues)}")
    for i in issues:
        print(" -", i)
    if not issues:
        print("RESULT: PASS — ready for import")
    return 0 if not issues else 1


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else r"c:\Users\62878\Downloads\gastrohub_master_menu_update.xlsx"
    raise SystemExit(main(path))
