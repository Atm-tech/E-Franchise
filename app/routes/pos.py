"""Pos HTTP routes."""
from fastapi import APIRouter

# Shared application services stay in app.main during the route extraction.
from app.core import *  # noqa: F401,F403

router = APIRouter()


@router.get("/pos/home", response_class=HTMLResponse)
def pos_home(request: Request):
    user = require_role(request, "POS")
    if isinstance(user, RedirectResponse):
        return user
    with get_conn() as conn:
        orders = conn.execute(
            """
            SELECT o.*, f.name as franchisee_name, f.shop_name,
                   (SELECT COALESCE(SUM(oi.qty),0) FROM order_items oi WHERE oi.order_id=o.id) item_count,
                   (SELECT COALESCE(SUM(pi.qty),0) FROM order_items oi LEFT JOIN picked_items pi ON pi.order_item_id=oi.id WHERE oi.order_id=o.id) picked_count,
                   ps.user_id picker_id, pu.name picker_name, ps.expires_at pick_expires_at
            FROM orders o
            JOIN franchisees f ON f.id=o.franchisee_id
            LEFT JOIN pick_sessions ps ON ps.order_id=o.id
            LEFT JOIN users pu ON pu.id=ps.user_id
            WHERE o.status='EXECUTED' AND o.outlet_id=? AND o.id NOT IN (SELECT order_id FROM bills)
            ORDER BY o.created_at ASC, o.id ASC
            """, (user["outlet_id"] or 1,)
        ).fetchall()
        order_items = conn.execute(
            """
            SELECT oi.id, oi.order_id, oi.product_name_snapshot, oi.pack_label, oi.qty,
                   p.sku_code, COALESCE(pi.qty,0) picked
            FROM order_items oi
            JOIN orders o ON o.id=oi.order_id
            JOIN products p ON p.id=oi.product_id
            LEFT JOIN picked_items pi ON pi.order_item_id=oi.id
            WHERE o.outlet_id=? AND o.status='EXECUTED'
              AND o.id NOT IN (SELECT order_id FROM bills)
            ORDER BY oi.id
            """, (user["outlet_id"] or 1,)
        ).fetchall()
        open_bills = conn.execute(
            """
            SELECT b.*, f.name as franchisee_name, o.fulfillment_mode, o.customer_name, o.total
            FROM bills b JOIN franchisees f ON f.id=b.franchisee_id JOIN orders o ON o.id=b.order_id
            WHERE b.outlet_id=? AND NOT EXISTS(SELECT 1 FROM docket_bills db WHERE db.bill_id=b.id) AND b.status IN ('BILLED', 'PACKED_INDIVIDUALLY', 'DOCKET_CREATED', 'HANDED_TO_GUARD', 'AT_GUARD_DESK', 'WEIGHT_CHECK_ALERT', 'WEIGHT_CHECK_PASSED', 'DELIVERY_ASSIGNED')
            ORDER BY b.created_at ASC, b.id ASC
            """, (user["outlet_id"] or 1,)
        ).fetchall()
        dockets = conn.execute(
            """
            SELECT d.*, f.shop_name, MIN(o.fulfillment_mode) fulfillment_mode, COUNT(db.bill_id) bill_count,
                   group_concat(b.bill_number, ', ') bill_numbers
            FROM dockets d JOIN franchisees f ON f.id=d.franchisee_id
            LEFT JOIN docket_bills db ON db.docket_id=d.id LEFT JOIN bills b ON b.id=db.bill_id LEFT JOIN orders o ON o.id=b.order_id
            WHERE d.outlet_id=? GROUP BY d.id, f.shop_name ORDER BY d.created_at ASC, d.id ASC LIMIT 30
            """, (user["outlet_id"] or 1,)
        ).fetchall()
        outlet = conn.execute("SELECT * FROM outlets WHERE id=?", (user["outlet_id"] or 1,)).fetchone()
        outlet_franchises = conn.execute("SELECT id,shop_name FROM franchisees WHERE outlet_id=? ORDER BY shop_name", (user["outlet_id"] or 1,)).fetchall()
        picking_required = get_setting(conn.cursor(), "require_picking", "1") == "1"
    return templates.TemplateResponse("pos_home.html", {"request": request, "user": user, "orders": orders, "order_items": order_items, "outlet_franchises": outlet_franchises, "open_bills": open_bills, "dockets": dockets, "outlet": outlet, "picking_required": picking_required, "error": request.query_params.get("error", ""), "message": request.query_params.get("message", "")})


@router.post("/pos/bills")
def create_bill(request: Request, order_id: int = Form(...)) -> RedirectResponse:
    user = require_role(request, "POS")
    if isinstance(user, RedirectResponse):
        return user
    with get_conn() as conn:
        cur = conn.cursor()
        order = cur.execute("SELECT * FROM orders WHERE id=? AND status='EXECUTED' AND outlet_id=?", (order_id, user["outlet_id"] or 1)).fetchone()
        existing = cur.execute("SELECT 1 FROM bills WHERE order_id=?", (order_id,)).fetchone()
        if not order or existing:
            return RedirectResponse("/pos/home#queue", status_code=303)
        operations.require_picked(cur, order, user)
        operations.consume_order_inventory(cur, order_id, order["outlet_id"], user["id"])
        expected = cur.execute("SELECT COALESCE(SUM(line_weight_kg),0) w FROM order_items WHERE order_id=?", (order_id,)).fetchone()["w"]
        bill_number = next_code("BILL-", "bills", "bill_number")
        cur.execute(
            """
            INSERT INTO bills
            (bill_number, order_id, franchisee_id, outlet_id, status, expected_weight_kg, created_at)
            VALUES (?, ?, ?, ?, 'BILLED', ?, ?)
            """,
            (bill_number, order_id, order["franchisee_id"], order["outlet_id"], expected, now_str()),
        )
        bill_id = cur.lastrowid
        add_order_event(cur, order_id, "BILLED", bill_number, user["id"])
        audit(cur, user, "CREATE", "BILL", bill_id, bill_number)
        conn.commit()
    return RedirectResponse("/pos/home#packing", status_code=303)


@router.post("/pos/dockets")
def create_docket(request: Request, bill_ids: str = Form(...), docket_id: int | None = Form(None)) -> RedirectResponse:
    user = require_role(request, "POS")
    if isinstance(user, RedirectResponse):
        return user
    selected = [int(x.strip()) for x in bill_ids.split(",") if x.strip().isdigit()]
    if not selected or len(selected) > 100:
        return RedirectResponse("/pos/home#packing", status_code=303)
    with get_conn() as conn:
        cur = conn.cursor()
        rows = cur.execute(
            f"""SELECT b.*, o.fulfillment_mode FROM bills b JOIN orders o ON o.id=b.order_id
                 WHERE b.id IN ({','.join(['?'] * len(selected))}) AND b.outlet_id=?
                   AND b.status='PACKED_INDIVIDUALLY'
                   AND b.id NOT IN (SELECT bill_id FROM docket_bills)""",
            tuple(selected) + (user["outlet_id"] or 1,),
        ).fetchall()
        if len(rows) != len(set(selected)):
            return RedirectResponse("/pos/home?error=Select+only+packed+unassigned+bills#packing", status_code=303)
        if len(rows) > int(get_setting(cur, "max_bills_per_docket", "30")):
            raise HTTPException(400, "Docket bill limit exceeded")
        franchise_ids = {r["franchisee_id"] for r in rows}
        fulfillment_modes = {r["fulfillment_mode"] for r in rows}
        if len(franchise_ids) != 1 or len(fulfillment_modes) != 1:
            return RedirectResponse("/pos/home?error=Docket+bills+must+share+one+franchise+and+fulfilment+mode#packing", status_code=303)
        if docket_id:
            dispatch_rules.append_docket(cur, docket_id, rows, user)
        else:
            dispatch_rules.make_docket(cur, rows, user)
        conn.commit()
    return RedirectResponse("/pos/home?message=Docket+created#dockets", status_code=303)


@router.post("/pos/handover")
def pos_handover(
    request: Request,
    entity_type: str = Form(...),
    entity_id: int = Form(...),
) -> RedirectResponse:
    user = require_role(request, "POS")
    if isinstance(user, RedirectResponse):
        return user
    entity_type = entity_type.upper()
    if entity_type not in {"BILL", "DOCKET"}:
        raise HTTPException(400, "Invalid dispatch type")
    table = "bills" if entity_type == "BILL" else "dockets"
    with get_conn() as conn:
        cur = conn.cursor()
        row = cur.execute(f"SELECT * FROM {table} WHERE id=? AND outlet_id=?", (entity_id, user["outlet_id"] or 1)).fetchone()
        allowed = row and ((entity_type == "BILL" and row["status"] == "PACKED_INDIVIDUALLY") or (entity_type == "DOCKET" and row["status"] == "DOCKET_CREATED"))
        if not allowed:
            return RedirectResponse("/pos/home?error=Entity+is+not+ready+for+guard#" + ("dockets" if entity_type == "DOCKET" else "packing"), status_code=303)
        if entity_type == "BILL":
            entity_type, entity_id = dispatch_rules.prepare_handover(cur, row, user)
            table = "bills" if entity_type == "BILL" else "dockets"
        cur.execute(f"UPDATE {table} SET status='HANDED_TO_GUARD', updated_at=? WHERE id=?", (now_str(), entity_id))
        if entity_type == "DOCKET":
            cur.execute("UPDATE bills SET status='HANDED_TO_GUARD', updated_at=? WHERE id IN (SELECT bill_id FROM docket_bills WHERE docket_id=?)", (now_str(), entity_id))
        cur.execute(
            "INSERT INTO handover_logs (entity_type, entity_id, from_role, to_role, notes, created_at, from_user_id, handover_mode) VALUES (?, ?, 'POS', 'GUARD', ?, ?, ?, 'MANUAL')",
            (entity_type, entity_id, "Handed over from POS", now_str(), user["id"]),
        )
        for order_id in order_ids_for_entity(cur, entity_type, entity_id):
            add_order_event(cur, order_id, "HANDED_TO_GUARD", "Awaiting guard receipt scan", user["id"])
        audit(cur, user, "HANDOVER", entity_type, entity_id, "POS to GUARD")
        conn.commit()
    return RedirectResponse("/pos/home?message=Handed+to+guard#" + ("dockets" if entity_type == "DOCKET" else "packing"), status_code=303)


@router.post("/pos/bills/{bill_id}/pack")
def pack_bill(bill_id: int, request: Request) -> RedirectResponse:
    user = require_role(request, "POS")
    if isinstance(user, RedirectResponse):
        return user
    with get_conn() as conn:
        cur = conn.cursor()
        bill = cur.execute("SELECT * FROM bills WHERE id=? AND outlet_id=? AND status='BILLED'", (bill_id, user["outlet_id"] or 1)).fetchone()
        if bill:
            cur.execute("UPDATE bills SET status='PACKED_INDIVIDUALLY', pack_status='PACKED_INDIVIDUALLY', updated_at=? WHERE id=?", (now_str(), bill_id))
            add_order_event(cur, bill["order_id"], "PACKED_INDIVIDUALLY", bill["bill_number"], user["id"])
            audit(cur, user, "PACK", "BILL", bill_id, bill["bill_number"])
            conn.commit()
    return RedirectResponse("/pos/home?message=Bill+packed+individually#packing", status_code=303)
