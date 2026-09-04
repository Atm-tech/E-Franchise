"""End-to-end lifecycle smoke test. Run against a disposable database copy."""
import json
import os
import sqlite3
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.main as main


test_db = os.environ.get("E_FRANCHISE_TEST_DB")
if not test_db or not Path(test_db).name.endswith("_test.db"):
    raise RuntimeError("E_FRANCHISE_TEST_DB must point to a disposable *_test.db file")
main.DB_PATH = Path(test_db)


def login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/login", data={"phone": username, "pin": password}, follow_redirects=True)
    assert response.status_code == 200, (username, response.status_code)


with TestClient(main.app) as client:
    login(client, "frn", "frn@123")
    home = client.get("/franchise/home")
    assert home.status_code == 200 and home.text.count('data-power-pick="1"') == 3
    assert "1 L pouch" in home.text and "1 unit · 2200W" in home.text
    with sqlite3.connect(main.DB_PATH) as db:
        db.row_factory = sqlite3.Row
        product = db.execute("SELECT id, image_url FROM products WHERE status='ACTIVE' ORDER BY rsp LIMIT 1").fetchone()
    image = client.get(product["image_url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/webp"

    response = client.post(
        "/franchise/orders",
        data={
            "cart_json": json.dumps([{"id": product["id"], "qty": 1}]),
            "customer_name": "E2E Customer",
            "customer_phone": "9999999999",
            "customer_address": "E2E Test Address Sector 12",
            "fulfillment_mode": "DELIVERY_MAN",
            "delivery_type": "DAY",
            "distance_km": "1",
            "status_mode": "EXECUTED",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with sqlite3.connect(main.DB_PATH) as db:
        db.row_factory = sqlite3.Row
        order = db.execute("SELECT * FROM orders WHERE customer_name='E2E Customer' ORDER BY id DESC LIMIT 1").fetchone()
        franchise_destination = db.execute("SELECT COALESCE(NULLIF(delivery_address,''), address) FROM franchisees WHERE id=?", (order["franchisee_id"],)).fetchone()[0]
        assert order and order["status"] == "EXECUTED" and order["customer_address"] == franchise_destination
        order_id = order["id"]

    client.get("/logout")
    login(client, "pos", "pos@123")
    assert client.post("/pos/bills", data={"order_id": order_id}, follow_redirects=False).status_code == 303
    with sqlite3.connect(main.DB_PATH) as db:
        db.row_factory = sqlite3.Row
        bill = db.execute("SELECT * FROM bills WHERE order_id=?", (order_id,)).fetchone()
        bill_id, expected = bill["id"], bill["expected_weight_kg"]
        tax_item = db.execute("SELECT * FROM order_items WHERE order_id=? LIMIT 1", (order_id,)).fetchone()
        assert tax_item["taxable_value"] + tax_item["gst_amount"] == tax_item["line_total"]
        assert tax_item["slab_eligible"] in (0, 1)
    client.get("/logout")
    login(client, "frn", "frn@123")
    invoice = client.get(f"/franchise/bills/{bill_id}")
    assert invoice.status_code == 200 and "WhatsApp customer" in invoice.text
    assert "commission" not in invoice.text.lower()
    summary_pdf = client.get("/franchise/summary.pdf?period=month")
    assert summary_pdf.status_code == 200 and summary_pdf.headers["content-type"] == "application/pdf"
    client.get("/logout")
    login(client, "pos", "pos@123")
    client.post(f"/pos/bills/{bill_id}/pack", follow_redirects=False)
    client.post("/pos/handover", data={"entity_type": "BILL", "entity_id": bill_id}, follow_redirects=False)

    client.get("/logout")
    login(client, "grd", "grd@123")
    client.post("/guard/weight-check", data={"entity_type": "BILL", "entity_id": bill_id, "actual_weight_kg": expected}, follow_redirects=False)
    client.post("/guard/handover", data={"entity_type": "BILL", "entity_id": bill_id, "target_role": "DELIVERY"}, follow_redirects=False)
    with sqlite3.connect(main.DB_PATH) as db:
        db.row_factory = sqlite3.Row
        task = db.execute("SELECT * FROM delivery_tasks WHERE entity_type='BILL' AND entity_id=? ORDER BY id DESC LIMIT 1", (bill_id,)).fetchone()
        task_id = task["id"]

    client.get("/logout")
    login(client, "dlv", "dlv@123")
    for status in ("ACCEPTED", "PICKED_UP", "OUT_FOR_DELIVERY"):
        assert client.post("/delivery/tasks", data={"task_id": task_id, "status": status}, follow_redirects=False).status_code == 303
    assert client.post(
        "/delivery/tasks",
        data={"task_id": task_id, "status": "DELIVERED", "pod_type": "OTP", "pod_reference": "1234", "remarks": "E2E verified"},
        follow_redirects=False,
    ).status_code == 303

    with sqlite3.connect(main.DB_PATH) as db:
        db.row_factory = sqlite3.Row
        final_order = db.execute("SELECT status FROM orders WHERE id=?", (order_id,)).fetchone()
        final_bill = db.execute("SELECT status, weight_check_status FROM bills WHERE id=?", (bill_id,)).fetchone()
        final_task = db.execute("SELECT status, pod_reference FROM delivery_tasks WHERE id=?", (task_id,)).fetchone()
        debits = db.execute("SELECT COUNT(*) FROM wallet_ledger WHERE reference_type='ORDER' AND reference_id=? AND entry_type='ORDER_DEBIT'", (order_id,)).fetchone()[0]
        events = db.execute("SELECT COUNT(*) FROM order_events WHERE order_id=?", (order_id,)).fetchone()[0]
    assert final_order["status"] == final_bill["status"] == final_task["status"] == "DELIVERED"
    assert final_bill["weight_check_status"] == "PASS" and final_task["pod_reference"] == "1234"
    assert debits == 1 and events >= 8

    # Hold -> admin execute -> cancel must debit and refund exactly once.
    client.get("/logout")
    login(client, "frn", "frn@123")
    with sqlite3.connect(main.DB_PATH) as db:
        balance_before = db.execute("SELECT wallet_balance FROM franchisees ORDER BY id LIMIT 1").fetchone()[0]
    client.post(
        "/franchise/orders",
        data={"cart_json": json.dumps([{"id": product["id"], "qty": 1}]), "customer_name": "Hold Customer",
              "customer_phone": "9999999998", "customer_address": "Hold Test Address Sector 12",
              "fulfillment_mode": "FRANCHISE_PICKUP", "delivery_type": "DAY", "status_mode": "ON_HOLD"},
        follow_redirects=False,
    )
    with sqlite3.connect(main.DB_PATH) as db:
        db.row_factory = sqlite3.Row
        held = db.execute("SELECT * FROM orders WHERE customer_name='Hold Customer' ORDER BY id DESC LIMIT 1").fetchone()
        held_id = held["id"]
        assert held["status"] == "ON_HOLD"
    client.get("/logout")
    login(client, "adm", "adm@123")
    client.post(f"/admin/orders/{held_id}/status", data={"status": "EXECUTED"}, follow_redirects=False)
    client.post(f"/admin/orders/{held_id}/status", data={"status": "CANCELLED", "reason": "E2E refund test"}, follow_redirects=False)
    with sqlite3.connect(main.DB_PATH) as db:
        db.row_factory = sqlite3.Row
        held_final = db.execute("SELECT status FROM orders WHERE id=?", (held_id,)).fetchone()
        balance_after = db.execute("SELECT wallet_balance FROM franchisees ORDER BY id LIMIT 1").fetchone()[0]
        hold_entries = db.execute("SELECT entry_type FROM wallet_ledger WHERE reference_type='ORDER' AND reference_id=? ORDER BY id", (held_id,)).fetchall()
        assert held_final["status"] == "CANCELLED" and round(balance_after, 2) == round(balance_before, 2)
        assert [row["entry_type"] for row in hold_entries] == ["ORDER_DEBIT", "ORDER_REFUND"]

    # Admin network, onboarding, wallet, commission, and deal modules.
    client.post("/admin/outlets", data={"name": "E2E Outlet", "code": "E2E-OUT", "address": "Test Road", "area": "Test Area", "phone": "1800000000"}, follow_redirects=False)
    with sqlite3.connect(main.DB_PATH) as db:
        outlet_id = db.execute("SELECT id FROM outlets WHERE code='E2E-OUT'").fetchone()[0]
    client.post("/admin/users", data={"name": "E2E POS", "role": "POS", "phone": "e2epos", "pin": "1234", "outlet_id": outlet_id}, follow_redirects=False)
    client.post("/admin/franchisees", data={"name": "E2E Partner", "mobile": "9888888888", "shop_name": "E2E Shop", "address": "Test Address", "area": "Test Area", "advance_amount": 1000, "outlet_id": outlet_id}, follow_redirects=False)
    with sqlite3.connect(main.DB_PATH) as db:
        db.row_factory = sqlite3.Row
        e2e_partner = db.execute("SELECT * FROM franchisees WHERE mobile='9888888888'").fetchone()
        assert e2e_partner and e2e_partner["wallet_balance"] == 750
        e2e_partner_id = e2e_partner["id"]
    client.post(f"/admin/franchisees/{e2e_partner_id}/wallet", data={"adjustment_type": "MANUAL_DEBIT", "amount": 50, "remarks": "E2E debit"}, follow_redirects=False)
    client.post(f"/admin/franchisees/{e2e_partner_id}/advance", data={"advance_amount": 200, "received_mode": "BANK"}, follow_redirects=False)
    client.post(f"/admin/franchisees/{e2e_partner_id}/delivery-address", data={"delivery_address": "Updated Franchise Destination", "outlet_id": outlet_id}, follow_redirects=False)
    client.post("/admin/commissions", data={"scope": "PRODUCT", "product_id": product["id"], "commission_type": "PERCENT", "commission_value": 10}, follow_redirects=False)
    client.post("/admin/promotions", data={"name": "E2E Deal", "scope": "PRODUCT", "product_id": product["id"], "discount_type": "FIXED", "discount_value": 1}, follow_redirects=False)
    with sqlite3.connect(main.DB_PATH) as db:
        assert db.execute("SELECT wallet_balance FROM franchisees WHERE id=?", (e2e_partner_id,)).fetchone()[0] == 850
        assert db.execute("SELECT delivery_address FROM franchisees WHERE id=?", (e2e_partner_id,)).fetchone()[0] == "Updated Franchise Destination"
        assert db.execute("SELECT COUNT(*) FROM users WHERE phone='e2epos'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM commission_rules WHERE product_id=?", (product["id"],)).fetchone()[0] >= 1
        assert db.execute("SELECT COUNT(*) FROM promotions WHERE product_id=?", (product["id"],)).fetchone()[0] >= 1

    # Two individually packed bills can be grouped and handed over as one docket.
    client.get("/logout")
    login(client, "frn", "frn@123")
    docket_order_ids = []
    for index in (1, 2):
        client.post(
            "/franchise/orders",
            data={"cart_json": json.dumps([{"id": product["id"], "qty": 1}]), "customer_name": f"Docket Customer {index}",
                  "customer_phone": f"999999990{index}", "customer_address": f"Docket Test Address {index} Sector 12",
                  "fulfillment_mode": "FRANCHISE_PICKUP", "delivery_type": "DAY", "status_mode": "EXECUTED"},
            follow_redirects=False,
        )
    with sqlite3.connect(main.DB_PATH) as db:
        docket_order_ids = [row[0] for row in db.execute("SELECT id FROM orders WHERE customer_name LIKE 'Docket Customer %' ORDER BY id").fetchall()]
    client.get("/logout")
    login(client, "pos", "pos@123")
    docket_bill_ids = []
    for docket_order_id in docket_order_ids:
        client.post("/pos/bills", data={"order_id": docket_order_id}, follow_redirects=False)
        with sqlite3.connect(main.DB_PATH) as db:
            docket_bill_id = db.execute("SELECT id FROM bills WHERE order_id=?", (docket_order_id,)).fetchone()[0]
        docket_bill_ids.append(docket_bill_id)
        client.post(f"/pos/bills/{docket_bill_id}/pack", follow_redirects=False)
    client.post("/pos/dockets", data={"bill_ids": ",".join(map(str, docket_bill_ids))}, follow_redirects=False)
    with sqlite3.connect(main.DB_PATH) as db:
        db.row_factory = sqlite3.Row
        docket = db.execute("SELECT * FROM dockets ORDER BY id DESC LIMIT 1").fetchone()
        docket_id, docket_weight = docket["id"], docket["expected_weight_kg"]
        assert db.execute("SELECT COUNT(*) FROM docket_bills WHERE docket_id=?", (docket_id,)).fetchone()[0] == 2
    client.post("/pos/handover", data={"entity_type": "DOCKET", "entity_id": docket_id}, follow_redirects=False)
    client.get("/logout")
    login(client, "grd", "grd@123")
    client.post("/guard/weight-check", data={"entity_type": "DOCKET", "entity_id": docket_id, "actual_weight_kg": docket_weight}, follow_redirects=False)
    client.post("/guard/handover", data={"entity_type": "DOCKET", "entity_id": docket_id, "target_role": "FRANCHISEE"}, follow_redirects=False)
    with sqlite3.connect(main.DB_PATH) as db:
        docket_order_states = [row[0] for row in db.execute(f"SELECT status FROM orders WHERE id IN ({','.join('?' * len(docket_order_ids))})", docket_order_ids).fetchall()]
        assert docket_order_states == ["DELIVERED", "DELIVERED"]

    print(f"E2E_PASS order={order_id} bill={bill_id} task={task_id} events={events} wallet_debits={debits} held_refund=PASS admin_modules=PASS docket={docket_id}")
