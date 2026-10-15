"""Add clearly labeled dummy stock to one local E-Franchise pilot outlet."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if os.getenv("DATABASE_URL", "").strip() or os.getenv("APP_ENV", "").lower() == "production":
    raise SystemExit("Refusing to seed dummy inventory into a remote or production database.")

from app.main import get_conn, init_db, now_str

init_db()
PILOT_REFERENCE = "DEMO-PILOT-OPENING-OUT-01-20261012"
DEMO_QTY_PER_SKU = 20

with get_conn() as conn:
    cur = conn.cursor()
    franchise = cur.execute(
        "SELECT id,shop_name,outlet_id FROM franchisees WHERE status='ACTIVE' ORDER BY id LIMIT 1"
    ).fetchone()
    if not franchise or not franchise["outlet_id"]:
        raise SystemExit("No active franchise mapped to an outlet. Set up the pilot pair first.")
    outlet = cur.execute(
        "SELECT id,code,name FROM outlets WHERE id=? AND status='ACTIVE'",
        (franchise["outlet_id"],),
    ).fetchone()
    if not outlet:
        raise SystemExit("The pilot franchise outlet is not active.")
    admin = cur.execute("SELECT id FROM users WHERE role='ADMIN' AND status='ACTIVE' ORDER BY id LIMIT 1").fetchone()
    products = cur.execute("SELECT id,sku_code,product_name FROM products WHERE status='ACTIVE' ORDER BY id").fetchall()
    if not admin or not products:
        raise SystemExit("An active admin and at least one active product are required.")

    seeded = []
    for product in products:
        exists = cur.execute(
            "SELECT 1 FROM inventory_movements WHERE outlet_id=? AND product_id=? AND kind='DEMO_OPENING' AND reference=?",
            (outlet["id"], product["id"], PILOT_REFERENCE),
        ).fetchone()
        if exists:
            continue
        timestamp = now_str()
        cur.execute(
            "INSERT INTO inventory_balances(outlet_id,product_id,on_hand,reserved,updated_at) VALUES(?,?,?,0,?) "
            "ON CONFLICT(outlet_id,product_id) DO UPDATE SET on_hand=inventory_balances.on_hand+excluded.on_hand,updated_at=excluded.updated_at",
            (outlet["id"], product["id"], DEMO_QTY_PER_SKU, timestamp),
        )
        cur.execute(
            "INSERT INTO inventory_movements(outlet_id,product_id,kind,quantity_delta,reserved_delta,reference,reason,created_by,created_at) "
            "VALUES(?,?,'DEMO_OPENING',?,0,?,?,?,?)",
            (outlet["id"], product["id"], DEMO_QTY_PER_SKU, PILOT_REFERENCE,
             "DEMO ONLY: dummy opening stock for local one-outlet/one-franchise order-flow practice; reconcile before real operations.",
             admin["id"], timestamp),
        )
        seeded.append((product["sku_code"], product["product_name"], DEMO_QTY_PER_SKU))

    conn.commit()
    print(f"Pilot outlet: {outlet['code']} - {outlet['name']}")
    print(f"Pilot franchise: {franchise['shop_name']} (#{franchise['id']})")
    print(f"Seeded {len(seeded)} products at {DEMO_QTY_PER_SKU} dummy packs/units each.")
    for sku, name, qty in seeded:
        print(f"  {sku} | {name} | +{qty}")
    if not seeded:
        print("No rows added; this pilot stock seed was already applied.")
    print("All rows are marked DEMO ONLY in the append-only stock ledger.")
