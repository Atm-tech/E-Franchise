"""Guard HTTP routes."""
from fastapi import APIRouter

# Shared application services stay in app.main during the route extraction.
from app.core import *  # noqa: F401,F403

router = APIRouter()


@router.get("/guard/home", response_class=HTMLResponse)
def guard_home(request: Request):
    user = require_role(request, "GUARD")
    if isinstance(user, RedirectResponse):
        return user
    with get_conn() as conn:
        bills = conn.execute(
            """SELECT b.*, o.customer_name, o.fulfillment_mode, f.shop_name
               FROM bills b JOIN orders o ON o.id=b.order_id JOIN franchisees f ON f.id=b.franchisee_id
               WHERE b.outlet_id=? AND b.status IN ('AT_GUARD_DESK','WEIGHT_CHECK_ALERT','WEIGHT_CHECK_PASSED','DELIVERY_ASSIGNED')
                 AND b.id NOT IN (SELECT bill_id FROM docket_bills) ORDER BY b.id DESC""", (user["outlet_id"] or 1,)
        ).fetchall()
        dockets = conn.execute(
            """SELECT d.*, f.shop_name, COUNT(db.bill_id) bill_count, group_concat(b.bill_number, ', ') bill_numbers,
                      MIN(o.fulfillment_mode) fulfillment_mode
               FROM dockets d JOIN franchisees f ON f.id=d.franchisee_id
               LEFT JOIN docket_bills db ON db.docket_id=d.id LEFT JOIN bills b ON b.id=db.bill_id
               LEFT JOIN orders o ON o.id=b.order_id
               WHERE d.outlet_id=? AND d.status IN ('AT_GUARD_DESK','WEIGHT_CHECK_ALERT','WEIGHT_CHECK_PASSED','DELIVERY_ASSIGNED')
               GROUP BY d.id, f.shop_name ORDER BY d.id DESC""", (user["outlet_id"] or 1,)
        ).fetchall()
        incoming = conn.execute(
            """SELECT 'BILL' entity_type,b.bill_number number,f.shop_name,o.customer_name,1 bill_count,b.created_at AS created_at
               FROM bills b JOIN orders o ON o.id=b.order_id JOIN franchisees f ON f.id=b.franchisee_id
               WHERE b.outlet_id=? AND b.status='HANDED_TO_GUARD' AND b.id NOT IN (SELECT bill_id FROM docket_bills)
               UNION ALL
               SELECT 'DOCKET',d.docket_number,f.shop_name,'Grouped dispatch',COUNT(db.bill_id),d.created_at
               FROM dockets d JOIN franchisees f ON f.id=d.franchisee_id
               LEFT JOIN docket_bills db ON db.docket_id=d.id
               WHERE d.outlet_id=? AND d.status='HANDED_TO_GUARD'
               GROUP BY d.id, f.shop_name ORDER BY created_at DESC""", (user["outlet_id"] or 1, user["outlet_id"] or 1)
        ).fetchall()
        tolerance = conn.execute("SELECT value FROM settings WHERE key='weight_tolerance_kg'").fetchone()["value"]
        blocking = get_setting(conn.cursor(), "weight_alert_is_blocking", "0") == "1"
        require_seal_check = get_setting(conn.cursor(), "require_seal_check", "1") == "1"
    return templates.TemplateResponse(
        "guard_home.html",
        {"request": request, "user": user, "bills": bills, "dockets": dockets, "incoming": incoming,
         "tolerance": tolerance, "blocking": blocking, "require_seal_check": require_seal_check,
         "error": request.query_params.get("error", "")},
    )


@router.post("/guard/weight-check")
def guard_weight_check(
    request: Request,
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    actual_weight_kg: float = Form(...),
) -> RedirectResponse:
    user = require_role(request, "GUARD")
    if isinstance(user, RedirectResponse):
        return user
    if entity_type not in {"BILL", "DOCKET"}:
        raise HTTPException(400, "Invalid dispatch type")
    table = "bills" if entity_type == "BILL" else "dockets"
    with get_conn() as conn:
        cur = conn.cursor()
        row = cur.execute(f"SELECT * FROM {table} WHERE id=? AND outlet_id=?", (entity_id, user["outlet_id"] or 1)).fetchone()
        if not row or row["status"] not in {"AT_GUARD_DESK", "WEIGHT_CHECK_ALERT", "WEIGHT_CHECK_PASSED"}:
            return RedirectResponse("/guard/home", status_code=303)
        if entity_type == "BILL" and cur.execute("SELECT 1 FROM docket_bills WHERE bill_id=?", (entity_id,)).fetchone():
            raise HTTPException(409, "Weigh the complete docket, not an individual bill")
        if not math.isfinite(actual_weight_kg) or actual_weight_kg < 0:
            raise HTTPException(400, "Weight must be a positive finite value")
        expected = float(row["expected_weight_kg"] or 0)
        variance = round(actual_weight_kg - expected, 3)
        tolerance = max(float(get_setting(cur, "weight_tolerance_kg", "0.3")), expected * float(get_setting(cur, "weight_tolerance_percent", "0")) / 100)
        check_status = "ALERT" if abs(variance) > tolerance else "PASS"
        state = "WEIGHT_CHECK_ALERT" if check_status == "ALERT" else "WEIGHT_CHECK_PASSED"
        cur.execute(
            f"UPDATE {table} SET actual_weight_kg=?, weight_variance_kg=?, weight_check_status=?, status=? WHERE id=?",
            (actual_weight_kg, variance, check_status, state, entity_id),
        )
        if entity_type == "DOCKET":
            cur.execute("UPDATE bills SET status=?, updated_at=? WHERE id IN (SELECT bill_id FROM docket_bills WHERE docket_id=?)", (state, now_str(), entity_id))
        for order_id in order_ids_for_entity(cur, entity_type, entity_id):
            add_order_event(cur, order_id, state, f"Weight {actual_weight_kg:.3f} kg; variance {variance:+.3f} kg", user["id"])
        audit(cur, user, "WEIGHT_CHECK", entity_type, entity_id, f"{check_status}: {variance:+.3f} kg")
        conn.commit()
    return RedirectResponse("/guard/home", status_code=303)


@router.post("/guard/handover")
def guard_handover(
    request: Request,
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    target_role: str = Form(...),
    receiver_code: str = Form(""),
) -> RedirectResponse:
    user = require_role(request, "GUARD")
    if isinstance(user, RedirectResponse):
        return user

    if entity_type not in {"BILL", "DOCKET"}:
        raise HTTPException(400, "Invalid dispatch type")
    table = "bills" if entity_type == "BILL" else "dockets"
    with get_conn() as conn:
        cur = conn.cursor()
        row = cur.execute(f"SELECT * FROM {table} WHERE id=? AND outlet_id=?", (entity_id, user["outlet_id"] or 1)).fetchone()
        if not row:
            return RedirectResponse("/guard/home", status_code=303)
        blocking = get_setting(cur, "weight_alert_is_blocking", "0") == "1"
        if row["weight_check_status"] == "PENDING" or (blocking and row["weight_check_status"] == "ALERT"):
            return RedirectResponse("/guard/home?error=Weight+check+must+pass+before+handover", status_code=303)
        if row["seal_status"] == "BROKEN" or (get_setting(cur, "require_seal_check", "1") == "1" and row["seal_status"] != "INTACT"):
            return RedirectResponse("/guard/home?error=Confirm+an+intact+seal+before+handover", status_code=303)

        operations.validate_release(cur, row, entity_type, target_role, receiver_code, user)
        if target_role == "DELIVERY":
            existing = cur.execute("SELECT 1 FROM delivery_tasks WHERE entity_type=? AND entity_id=? AND status!='DELIVERED'", (entity_type, entity_id)).fetchone()
            delivery_user = cur.execute("SELECT id FROM users WHERE role='DELIVERY' AND status='ACTIVE' AND outlet_id=? ORDER BY id LIMIT 1", (user["outlet_id"] or 1,)).fetchone()
            if not existing:
                cur.execute(
                    "INSERT INTO delivery_tasks (entity_type, entity_id, franchisee_id, status, created_at, assigned_user_id) VALUES (?, ?, ?, 'ASSIGNED', ?, ?)",
                    (entity_type, entity_id, row["franchisee_id"], now_str(), delivery_user["id"] if delivery_user else None),
                )
            next_status = "DELIVERY_ASSIGNED"
        else:
            next_status = "PICKED_BY_FRANCHISEE"
        cur.execute(f"UPDATE {table} SET status=?, updated_at=? WHERE id=?", (next_status, now_str(), entity_id))
        if entity_type == "DOCKET":
            cur.execute("UPDATE bills SET status=?, updated_at=? WHERE id IN (SELECT bill_id FROM docket_bills WHERE docket_id=?)", (next_status, now_str(), entity_id))
        cur.execute(
            "INSERT INTO handover_logs (entity_type, entity_id, from_role, to_role, notes, created_at, from_user_id, handover_mode) VALUES (?, ?, 'GUARD', ?, ?, ?, ?, 'MANUAL')",
            (entity_type, entity_id, target_role, "Guard handover", now_str(), user["id"]),
        )
        for order_id in order_ids_for_entity(cur, entity_type, entity_id):
            add_order_event(cur, order_id, next_status, f"Guard handover to {target_role}", user["id"])
        if target_role != "DELIVERY":
            complete_entity_orders(cur, entity_type, entity_id, user["id"])
        audit(cur, user, "HANDOVER", entity_type, entity_id, f"GUARD to {target_role}")
        conn.commit()
    return RedirectResponse("/guard/home", status_code=303)
