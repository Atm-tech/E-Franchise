"""Admin HTTP routes."""
from fastapi import APIRouter

# Shared application services stay in app.main during the route extraction.
from app.core import *  # noqa: F401,F403

router = APIRouter()


@router.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, message: str | None = None, error: str | None = None):
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user

    with get_conn() as conn:
        release_matured_commissions(conn.cursor())
        conn.commit()
        stats = {
            "pending_orders": conn.execute("SELECT COUNT(*) FROM orders WHERE status NOT IN ('DELIVERED','CANCELLED','PICKED_BY_FRANCHISEE')").fetchone()[0],
            "pending_franchisees": conn.execute("SELECT COUNT(*) FROM franchisees WHERE status='INACTIVE'").fetchone()[0],
            "franchisees": conn.execute("SELECT COUNT(1) c FROM franchisees WHERE status!='CANCELLED'").fetchone()["c"],
            "products": conn.execute("SELECT COUNT(1) c FROM products WHERE status='ACTIVE'").fetchone()["c"],
            "orders": conn.execute("SELECT COUNT(1) c FROM orders").fetchone()["c"],
            "bills": conn.execute("SELECT COUNT(1) c FROM bills").fetchone()["c"],
            "dockets": conn.execute("SELECT COUNT(1) c FROM dockets").fetchone()["c"],
            "weight_alerts": conn.execute(
                "SELECT COUNT(1) c FROM bills WHERE weight_check_status='ALERT'"
            ).fetchone()["c"]
            + conn.execute("SELECT COUNT(1) c FROM dockets WHERE weight_check_status='ALERT'").fetchone()["c"],
        }
        franchisees = conn.execute(
            """SELECT f.*, COALESCE(ew.balance,0) earnings_wallet_balance
               FROM franchisees f LEFT JOIN earnings_wallets ew ON ew.franchisee_id=f.id
               WHERE f.status!='CANCELLED' ORDER BY f.id DESC"""
        ).fetchall()
        products = conn.execute("SELECT * FROM products WHERE status='ACTIVE' ORDER BY product_name, sku_code").fetchall()
        inventory = conn.execute("SELECT ib.*,p.sku_code,p.product_name,o.code outlet_code FROM inventory_balances ib JOIN products p ON p.id=ib.product_id JOIN outlets o ON o.id=ib.outlet_id ORDER BY p.product_name,o.code LIMIT 150").fetchall()
        inventory_movements = conn.execute("SELECT im.*,p.sku_code,p.product_name,o.code outlet_code FROM inventory_movements im JOIN products p ON p.id=im.product_id JOIN outlets o ON o.id=im.outlet_id ORDER BY im.id DESC LIMIT 50").fetchall()
        orders = conn.execute(
            "SELECT o.*, f.shop_name FROM orders o JOIN franchisees f ON f.id=o.franchisee_id ORDER BY o.id DESC LIMIT 20"
        ).fetchall()
        orders = [dict(row) | {"return_window_open": bool(row["commission_available_at"] and row["commission_available_at"] > now_str() and not row["commission_released_at"])} for row in orders]
        order_return_items: dict[int, list[dict[str, Any]]] = {}
        return_items = conn.execute(
            """SELECT oi.id, oi.order_id, COALESCE(NULLIF(oi.product_name_snapshot,''),p.product_name) product_name,
                      oi.qty, oi.line_commission, COALESCE(r.returned_qty,0) returned_qty
               FROM order_items oi LEFT JOIN products p ON p.id=oi.product_id
               LEFT JOIN (SELECT order_item_id,SUM(quantity) returned_qty FROM order_returns GROUP BY order_item_id) r
                 ON r.order_item_id=oi.id ORDER BY oi.id"""
        ).fetchall()
        for item in return_items:
            row = dict(item)
            row["returnable_qty"] = int(row["qty"]) - int(row["returned_qty"])
            order_return_items.setdefault(int(row["order_id"]), []).append(row)
        wallet_ledger = conn.execute(
            "SELECT wl.*, f.shop_name FROM wallet_ledger wl JOIN franchisees f ON f.id=wl.franchisee_id ORDER BY wl.id DESC LIMIT 20"
        ).fetchall()
        outlets = conn.execute("SELECT * FROM outlets ORDER BY id").fetchall()
        staff = conn.execute("SELECT u.*, o.code outlet_code FROM users u LEFT JOIN outlets o ON o.id=u.outlet_id ORDER BY u.id DESC").fetchall()
        commission_rules = conn.execute(
            "SELECT cr.*, p.product_name FROM commission_rules cr LEFT JOIN products p ON p.id=cr.product_id ORDER BY cr.id DESC LIMIT 20"
        ).fetchall()
        promotions = conn.execute(
            "SELECT pr.*, p.product_name FROM promotions pr LEFT JOIN products p ON p.id=pr.product_id ORDER BY pr.id DESC LIMIT 20"
        ).fetchall()
        target_slabs = conn.execute(
            """SELECT tp.code, tp.name, tp.scope, tp.department, tp.product_id, tp.period_type, tp.timezone,
                      ts.threshold_amount, ts.reward_percent, ts.status
               FROM target_programs tp JOIN target_slabs ts ON ts.program_id=tp.id
               ORDER BY tp.id, ts.threshold_amount"""
        ).fetchall()
        dispatches = conn.execute(
            """
            SELECT 'BILL' entity_type, b.id entity_id, b.bill_number number, b.status, b.expected_weight_kg, b.actual_weight_kg,
                   b.weight_variance_kg, f.shop_name, o.code outlet_code
            FROM bills b JOIN franchisees f ON f.id=b.franchisee_id JOIN outlets o ON o.id=b.outlet_id
            UNION ALL
            SELECT 'DOCKET', d.id entity_id, d.docket_number, d.status, d.expected_weight_kg, d.actual_weight_kg,
                   d.weight_variance_kg, f.shop_name, o.code
            FROM dockets d JOIN franchisees f ON f.id=d.franchisee_id JOIN outlets o ON o.id=d.outlet_id
            ORDER BY 2 DESC LIMIT 20
            """
        ).fetchall()
        settings = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings").fetchall()}
        r2_values, r2_missing = r2_config()

    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "user": user,
            "stats": stats,
            "franchisees": franchisees,
            "products": products,
            "inventory": inventory,
            "inventory_movements": inventory_movements,
            "orders": orders,
            "order_return_items": order_return_items,
            "wallet_ledger": wallet_ledger,
            "outlets": outlets,
            "staff": staff,
            "commission_rules": commission_rules,
            "promotions": promotions,
            "target_slabs": target_slabs,
            "dispatches": dispatches,
            "settings": settings,
            "message": message,
            "error": error,
            "r2_ready": bool(r2_values),
            "r2_missing": r2_missing,
        },
    )


@router.post("/admin/franchisees")
def create_franchisee(
    request: Request,
    name: str = Form(...),
    mobile: str = Form(...),
    shop_name: str = Form(...),
    address: str = Form(""),
    delivery_address: str = Form(""),
    area: str = Form(""),
    advance_amount: float = Form(0),
    initial_password: str = Form(..., min_length=8),
    outlet_id: int = Form(1),
    bank_account_number: str = Form(""),
    ifsc_code: str = Form(""),
    bank_name: str = Form(""),
    gst_number: str = Form(""),
    latitude: str = Form(""),
    longitude: str = Form(""),
    request_key: str = Form(...),
) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    try:
        latitude_value = float(latitude.strip()) if latitude.strip() else None
        longitude_value = float(longitude.strip()) if longitude.strip() else None
    except ValueError:
        raise HTTPException(400, "Enter numeric latitude and longitude, or leave both empty")
    if (latitude_value is None) != (longitude_value is None):
        raise HTTPException(400, "Enter both latitude and longitude, or leave both empty")
    if latitude_value is not None and (not math.isfinite(latitude_value) or not math.isfinite(longitude_value)
                                       or not -90 <= latitude_value <= 90 or not -180 <= longitude_value <= 180):
        raise HTTPException(400, "Enter a valid location: latitude -90 to 90 and longitude -180 to 180")
    with get_conn() as conn:
        cur = conn.cursor()
        if not operations.claim_mutation(cur, user, "create_franchisee", request_key):
            return operations.redirect("This request was already processed. Review the ledger.")
        operations.positive(advance_amount, allow_zero=True)
        if not cur.execute("SELECT 1 FROM outlets WHERE id=? AND status='ACTIVE'", (outlet_id,)).fetchone():
            raise HTTPException(400, "Choose an active outlet")
        if cur.execute("SELECT 1 FROM users WHERE phone=?", (mobile.strip(),)).fetchone():
            raise HTTPException(409, "Login ID already exists")
        pct = 100.0
        wallet_credit = advance_amount * pct / 100.0
        cur.execute(
            """
            INSERT INTO franchisees
            (name, mobile, shop_name, address, area, wallet_balance, status, outlet_id, delivery_address, bank_account_number, ifsc_code, bank_name, gst_number, latitude, longitude, created_at)
            VALUES (?, ?, ?, ?, ?, 0, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name.strip(), mobile.strip(), shop_name.strip(), address.strip(), area.strip(), outlet_id,
             (delivery_address.strip() or address.strip()), bank_account_number.strip(), ifsc_code.strip().upper(), bank_name.strip(), gst_number.strip().upper(), latitude_value, longitude_value, now_str()),
        )
        fid = cur.lastrowid
        if advance_amount > 0:
            cur.execute(
                "INSERT INTO franchise_advances (franchisee_id, advance_amount, wallet_credit_percent, wallet_credited_amount, received_mode, received_at, created_by) VALUES (?, ?, ?, ?, 'BANK', ?, ?)",
                (fid, advance_amount, pct, wallet_credit, now_str(), user["id"]),
            )
            advance_id = cur.lastrowid
            wallet_entry(cur, fid, "ADVANCE_CREDIT", wallet_credit, "ADVANCE", advance_id, f"{pct:.0f}% of onboarding advance", user["id"])
        cur.execute(
            "INSERT INTO users (name, role, phone, pin, outlet_id, franchisee_id) VALUES (?, 'FRANCHISEE', ?, ?, ?, ?)",
            (name, mobile, hash_password(initial_password), outlet_id, fid),
        )
        audit(cur, user, "CREATE", "FRANCHISEE", fid, f"Onboarded {shop_name}")
        conn.commit()
    return RedirectResponse("/admin/dashboard?message=Franchisee+created#franchisees", status_code=303)


@router.post("/admin/products")
def create_product(
    request: Request,
    sku_code: str = Form(...),
    product_name: str = Form(...),
    department: str = Form(...),
    category: str = Form(""),
    subcategory: str = Form(""),
    hsn_code: str = Form(""),
    gst_rate: float = Form(0),
    slab_eligible: int = Form(1),
    mrp: float = Form(...),
    rsp: float = Form(...),
    unit_weight: float = Form(...),
    weight_unit: str = Form(...),
    pack_multiplier: int = Form(...),
    commission_mode: str = Form("DEPARTMENT"),
    commission_value: float = Form(0),
    brand: str = Form(""),
    attributes_json: str = Form("{}"),
    image: UploadFile | None = File(None),
) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    try:
        image_url = upload_product_image(image, sku_code) if image and image.filename else ""
        with get_conn() as conn:
            upsert_product(
                conn,
                {
                    "sku_code": sku_code,
                    "product_name": product_name,
                    "department": department,
                    "category": category,
                    "subcategory": subcategory,
                    "hsn_code": hsn_code,
                    "gst_rate": gst_rate,
                    "price_includes_gst": 1,
                    "slab_eligible": slab_eligible,
                    "mrp": mrp,
                    "rsp": rsp,
                    "unit_weight": unit_weight,
                    "weight_unit": weight_unit,
                    "pack_multiplier": pack_multiplier,
                    "commission_mode": commission_mode,
                    "commission_value": commission_value,
                    "brand": brand,
                    "attributes_json": attributes_json,
                    "status": "ACTIVE",
                    "image_url": image_url,
                },
            )
            conn.commit()
    except ValueError as exc:
        return RedirectResponse(f"/admin/dashboard?error={quote_plus(str(exc))}#catalog", status_code=303)
    return RedirectResponse("/admin/dashboard?message=Product+saved#catalog", status_code=303)


@router.post("/admin/products/image")
def upload_existing_product_image(
    request: Request,
    sku_code: str = Form(...),
    image: UploadFile = File(...),
) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user

    sku_code = sku_code.strip()
    with get_conn() as conn:
        product = conn.execute("SELECT id FROM products WHERE sku_code=?", (sku_code,)).fetchone()
    if not product:
        return RedirectResponse("/admin/dashboard?error=Product+SKU+not+found#catalog", status_code=303)

    try:
        image_url = upload_product_image(image, sku_code)
        with get_conn() as conn:
            conn.execute("UPDATE products SET image_url=? WHERE id=?", (image_url, product["id"]))
            conn.commit()
    except ValueError as exc:
        return RedirectResponse(f"/admin/dashboard?error={quote_plus(str(exc))}#catalog", status_code=303)

    return RedirectResponse("/admin/dashboard?message=Product+picture+uploaded#catalog", status_code=303)


@router.post("/admin/products/import")
def import_products(request: Request, file: UploadFile = File(...)) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user

    try:
        rows = parse_product_upload(file)
        if not rows:
            raise ValueError("No product rows found in the file.")

        with get_conn() as conn:
            for index, row in enumerate(rows, start=2):
                try:
                    upsert_product(conn, row)
                except ValueError as exc:
                    raise ValueError(f"Row {index}: {exc}") from exc
            conn.commit()
    except ValueError as exc:
        return RedirectResponse(f"/admin/dashboard?error={quote_plus(str(exc))}#catalog", status_code=303)
    except Exception:
        return RedirectResponse("/admin/dashboard?error=Could+not+import+the+file#catalog", status_code=303)

    return RedirectResponse(f"/admin/dashboard?message=Imported+{len(rows)}+products#catalog", status_code=303)


@router.post("/admin/settings")
def update_settings(
    request: Request,
    instant_premium_charge: float = Form(...),
    day_delivery_charge: float = Form(...),
    delivery_per_km: float = Form(8),
    delivery_pricing_mode: str = Form("PER_KM"),
    delivery_fixed_charge: float = Form(0),
    weight_tolerance_kg: float = Form(...),
    weight_alert_is_blocking: int = Form(0),
    free_delivery_threshold: float = Form(999),
    free_delivery_limit_per_day: int = Form(3),
    low_wallet_threshold: float = Form(1000),
    delivery_charge_bearer: str = Form("CUSTOMER"),
    business_legal_name: str | None = Form(None),
    business_gstin: str | None = Form(None),
    business_address: str | None = Form(None),
    business_state_code: str | None = Form(None),
) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    if delivery_pricing_mode not in {"PER_KM", "FIXED"}:
        raise HTTPException(400, "Invalid delivery pricing mode")
    operations.positive(delivery_fixed_charge, allow_zero=True)
    for value in (instant_premium_charge, day_delivery_charge, delivery_per_km, weight_tolerance_kg, free_delivery_threshold, low_wallet_threshold):
        operations.positive(value, allow_zero=True)
    if weight_alert_is_blocking not in (0, 1) or free_delivery_limit_per_day < 0 or delivery_charge_bearer not in {"CUSTOMER", "FRANCHISEE", "BUSINESS"}:
        raise HTTPException(400, "Invalid delivery rules")
    gstin = business_gstin.strip().upper() if business_gstin is not None else None
    state_code = business_state_code.strip() if business_state_code is not None else None
    if gstin and (len(gstin) != 15 or not gstin.isalnum()):
        return RedirectResponse("/admin/dashboard?error=GSTIN+must+be+15+letters+and+digits#rules", status_code=303)
    if state_code is not None and (len(state_code) != 2 or not state_code.isdigit()):
        return RedirectResponse("/admin/dashboard?error=GST+state+code+must+be+2+digits#rules", status_code=303)
    with get_conn() as conn:
        cur = conn.cursor()
        upsert_setting(cur, "instant_premium_charge", str(instant_premium_charge))
        upsert_setting(cur, "day_delivery_charge", str(day_delivery_charge))
        upsert_setting(cur, "delivery_pricing_mode", delivery_pricing_mode)
        upsert_setting(cur, "delivery_fixed_charge", str(delivery_fixed_charge))
        upsert_setting(cur, "delivery_per_km", str(delivery_per_km))
        upsert_setting(cur, "weight_tolerance_kg", str(weight_tolerance_kg))
        upsert_setting(cur, "weight_alert_is_blocking", str(weight_alert_is_blocking))
        upsert_setting(cur, "free_delivery_threshold", str(max(0, free_delivery_threshold)))
        upsert_setting(cur, "free_delivery_limit_per_day", str(max(0, free_delivery_limit_per_day)))
        upsert_setting(cur, "low_wallet_threshold", str(max(0, low_wallet_threshold)))
        upsert_setting(cur, "delivery_charge_bearer", delivery_charge_bearer)
        if business_legal_name is not None:
            upsert_setting(cur, "business_legal_name", business_legal_name.strip())
        if gstin is not None:
            upsert_setting(cur, "business_gstin", gstin)
        if business_address is not None:
            upsert_setting(cur, "business_address", business_address.strip())
        if state_code is not None:
            upsert_setting(cur, "business_state_code", state_code)
        audit(cur, user, "UPDATE", "SETTINGS", None, "Operational settings updated")
        conn.commit()
    return RedirectResponse("/admin/dashboard?message=Settings+saved#rules", status_code=303)


@router.post("/admin/tax-profile")
def update_tax_profile(
    request: Request,
    business_legal_name: str = Form(...),
    business_gstin: str = Form(""),
    business_address: str = Form(...),
    business_state_code: str = Form(...),
) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    gstin = business_gstin.strip().upper()
    state_code = business_state_code.strip()
    if gstin and (len(gstin) != 15 or not gstin.isalnum()):
        return RedirectResponse("/admin/dashboard?error=GSTIN+must+be+15+letters+and+digits#rules", status_code=303)
    if len(state_code) != 2 or not state_code.isdigit():
        return RedirectResponse("/admin/dashboard?error=GST+state+code+must+be+2+digits#rules", status_code=303)
    with get_conn() as conn:
        cur = conn.cursor()
        upsert_setting(cur, "business_legal_name", business_legal_name.strip())
        upsert_setting(cur, "business_gstin", gstin)
        upsert_setting(cur, "business_address", business_address.strip())
        upsert_setting(cur, "business_state_code", state_code)
        audit(cur, user, "UPDATE", "TAX_PROFILE", None, "Invoice supplier profile updated")
        conn.commit()
    return RedirectResponse("/admin/dashboard?message=Tax+profile+saved#rules", status_code=303)


@router.post("/admin/franchisees/{franchisee_id}/wallet")
def adjust_wallet(
    franchisee_id: int,
    request: Request,
    adjustment_type: str = Form(...),
    amount: float = Form(...),
    remarks: str = Form(...),
    request_key: str = Form(...),
) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    signed_amount = abs(amount) if adjustment_type == "MANUAL_CREDIT" else -abs(amount)
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            if not operations.claim_mutation(cur, user, "adjust_wallet", request_key):
                return operations.redirect("This request was already processed. Review the ledger.")
            wallet_entry(cur, franchisee_id, adjustment_type, signed_amount, "ADJUSTMENT", None, remarks.strip(), user["id"])
            audit(cur, user, adjustment_type, "WALLET", franchisee_id, f"{signed_amount:.2f}: {remarks}")
            conn.commit()
    except ValueError as exc:
        return RedirectResponse(f"/admin/dashboard?error={quote_plus(str(exc))}#franchisees", status_code=303)
    return RedirectResponse("/admin/dashboard?message=Wallet+updated#franchisees", status_code=303)


@router.post("/admin/franchisees/{franchisee_id}/test-wallet-topup")
def test_wallet_topup(
    franchisee_id: int,
    request: Request,
    amount: float = Form(...),
    request_key: str = Form(...),
) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    if os.getenv("APP_ENV") == "production":
        raise HTTPException(403, "Test credits are disabled in production")
    if not math.isfinite(amount) or amount <= 0 or amount > 100000:
        return RedirectResponse("/admin/dashboard?error=Test+top-up+must+be+between+1+and+100000#franchisees", status_code=303)
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            if not operations.claim_mutation(cur, user, "test_wallet_topup", request_key):
                return operations.redirect("This request was already processed. Review the ledger.")
            if get_setting(cur, "test_wallet_topup_enabled", "0") != "1":
                return RedirectResponse("/admin/dashboard?error=Test+wallet+top-up+is+disabled#franchisees", status_code=303)
            wallet_entry(cur, franchisee_id, "TEST_CREDIT", round(amount, 2), "TEST_MODE", None, "Admin test-period top-up", user["id"])
            audit(cur, user, "TEST_CREDIT", "WALLET", franchisee_id, f"Test-period top-up: {amount:.2f}")
            conn.commit()
    except ValueError as exc:
        return RedirectResponse(f"/admin/dashboard?error={quote_plus(str(exc))}#franchisees", status_code=303)
    return RedirectResponse("/admin/dashboard?message=Test+wallet+credited#franchisees", status_code=303)


@router.post("/admin/franchisees/{franchisee_id}/advance")
def record_advance(
    franchisee_id: int,
    request: Request,
    advance_amount: float = Form(...),
    received_mode: str = Form("BANK"),
    request_key: str = Form(...),
) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    if advance_amount <= 0:
        return RedirectResponse("/admin/dashboard?error=Advance+must+be+positive#franchisees", status_code=303)
    with get_conn() as conn:
        cur = conn.cursor()
        if not operations.claim_mutation(cur, user, "record_advance", request_key):
            return operations.redirect("This request was already processed. Review the ledger.")
        # Cash deposits are credited rupee-for-rupee to the purchase wallet.
        pct = 100.0
        credit = round(advance_amount, 2)
        cur.execute(
            "INSERT INTO franchise_advances (franchisee_id, advance_amount, wallet_credit_percent, wallet_credited_amount, received_mode, received_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (franchisee_id, advance_amount, pct, credit, received_mode, now_str(), user["id"]),
        )
        advance_id = cur.lastrowid
        wallet_entry(cur, franchisee_id, "CASH_DEPOSIT" if received_mode.upper() == "CASH" else "ADVANCE_CREDIT", credit, "ADVANCE", advance_id, f"{pct:.0f}% purchase-wallet credit ({received_mode.upper()})", user["id"])
        audit(cur, user, "RECORD_ADVANCE", "FRANCHISEE", franchisee_id, f"Advance {advance_amount:.2f}; credit {credit:.2f}")
        conn.commit()
    return RedirectResponse("/admin/dashboard?message=Advance+recorded#franchisees", status_code=303)


@router.post("/admin/franchisees/{franchisee_id}/status")
def change_franchise_status(franchisee_id: int, request: Request, status: str = Form(...)) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    status = "ACTIVE" if status == "ACTIVE" else "INACTIVE"
    with get_conn() as conn:
        cur = conn.cursor()
        lock = ' FOR UPDATE' if getattr(cur, 'is_postgres', False) else ''
        franchise = cur.execute("SELECT status FROM franchisees WHERE id=?" + lock, (franchisee_id,)).fetchone()
        if not franchise:
            raise HTTPException(404, "Franchise not found")
        if franchise['status'] == 'CANCELLED':
            raise HTTPException(409, "This membership has been cancelled and cannot be activated")
        cur.execute("UPDATE franchisees SET status=? WHERE id=?", (status, franchisee_id))
        audit(cur, user, "STATUS_CHANGE", "FRANCHISEE", franchisee_id, status)
        conn.commit()
    return RedirectResponse("/admin/dashboard?message=Franchisee+status+updated#franchisees", status_code=303)


@router.post('/admin/franchisees/{franchisee_id}/cancel-membership')
def cancel_franchise_membership(franchisee_id: int, request: Request, request_key: str = Form(...)) -> RedirectResponse:
    user = require_role(request, 'ADMIN')
    if isinstance(user, RedirectResponse):
        return user
    with get_conn() as conn:
        cur = conn.cursor()
        lock = ' FOR UPDATE' if getattr(cur, 'is_postgres', False) else ''
        franchise = cur.execute('SELECT * FROM franchisees WHERE id=?' + lock, (franchisee_id,)).fetchone()
        if not franchise:
            raise HTTPException(404, 'Franchise not found')
        if franchise['status'] == 'CANCELLED':
            return RedirectResponse('/admin/dashboard?message=Membership+already+cancelled#franchisees', status_code=303)
        if cur.execute("SELECT 1 FROM orders WHERE franchisee_id=? AND status NOT IN ('DELIVERED','CANCELLED','PICKED_BY_FRANCHISEE') LIMIT 1", (franchisee_id,)).fetchone():
            raise HTTPException(409, 'Close all active and on-hold orders before cancelling membership')
        if cur.execute("SELECT 1 FROM delivery_tasks WHERE franchisee_id=? AND status NOT IN ('DELIVERED','CANCELLED') LIMIT 1", (franchisee_id,)).fetchone():
            raise HTTPException(409, 'Close outstanding delivery tasks before cancelling membership')
        earnings = cur.execute('SELECT balance FROM earnings_wallets WHERE franchisee_id=?', (franchisee_id,)).fetchone()
        if round(float(franchise['wallet_balance']), 2) != 0 or (earnings and round(float(earnings['balance']), 2) != 0):
            raise HTTPException(409, 'Settle the purchase wallet and earnings wallet to zero before cancelling membership')
        if cur.execute("SELECT 1 FROM payment_transactions WHERE franchisee_id=? AND status='PENDING' LIMIT 1", (franchisee_id,)).fetchone():
            raise HTTPException(409, 'Resolve pending payments before cancelling membership')
        if cur.execute("""SELECT 1 FROM orders o WHERE o.franchisee_id=? AND o.status='DELIVERED'
            AND o.commission_released_at IS NULL AND o.commission_total > COALESCE((
                SELECT SUM(r.commission_reversed) FROM order_returns r WHERE r.order_id=o.id),0) LIMIT 1""", (franchisee_id,)).fetchone():
            raise HTTPException(409, 'Release and settle pending commissions before cancelling membership')
        if operations.claim_mutation(cur, user, f'cancel_franchise_membership:{franchisee_id}', request_key):
            cur.execute("UPDATE franchisees SET status='CANCELLED' WHERE id=?", (franchisee_id,))
            cur.execute("UPDATE users SET status='INACTIVE',auth_version=auth_version+1 WHERE franchisee_id=? AND role='FRANCHISEE'", (franchisee_id,))
            audit(cur, user, 'CANCEL_MEMBERSHIP', 'FRANCHISEE', franchisee_id,
                  f"Cancelled membership for {franchise['shop_name']}; login revoked; order and financial history retained")
        conn.commit()
    return RedirectResponse('/admin/dashboard?message=Membership+cancelled#franchisees', status_code=303)


@router.post("/admin/outlets")
def create_outlet(
    request: Request,
    name: str = Form(...), code: str = Form(...), address: str = Form(...), area: str = Form(...), phone: str = Form(""),
) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO outlets (name, code, address, area, phone) VALUES (?, ?, ?, ?, ?)",
                        (name.strip(), code.strip().upper(), address.strip(), area.strip(), phone.strip()))
            audit(cur, user, "CREATE", "OUTLET", cur.lastrowid, code)
            conn.commit()
    except sqlite3.IntegrityError:
        return RedirectResponse("/admin/dashboard?error=Outlet+code+already+exists#network", status_code=303)
    return RedirectResponse("/admin/dashboard?message=Outlet+created#network", status_code=303)


@router.post("/admin/franchisees/{franchisee_id}/delivery-address")
def update_franchise_delivery_address(
    franchisee_id: int,
    request: Request,
    delivery_address: str = Form(...),
    outlet_id: int = Form(...),
) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    if len(delivery_address.strip()) < 5:
        return RedirectResponse("/admin/dashboard?error=Enter+a+complete+franchise+delivery+address#franchisees", status_code=303)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE franchisees SET delivery_address=?, outlet_id=? WHERE id=?", (delivery_address.strip(), outlet_id, franchisee_id))
        cur.execute("UPDATE users SET outlet_id=? WHERE franchisee_id=? AND role='FRANCHISEE'", (outlet_id, franchisee_id))
        audit(cur, user, "UPDATE_DELIVERY_ADDRESS", "FRANCHISEE", franchisee_id, delivery_address.strip())
        conn.commit()
    return RedirectResponse("/admin/dashboard?message=Franchise+delivery+address+updated#franchisees", status_code=303)


@router.post("/admin/users")
def create_staff_user(
    request: Request,
    name: str = Form(...), role: str = Form(...), phone: str = Form(...), pin: str = Form(..., min_length=8), outlet_id: int = Form(1),
) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    if role not in {"POS", "GUARD", "DELIVERY", "ADMIN"}:
        return RedirectResponse("/admin/dashboard?error=Invalid+staff+role#network", status_code=303)
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            if role != "ADMIN" and not cur.execute("SELECT 1 FROM outlets WHERE id=? AND status='ACTIVE'", (outlet_id,)).fetchone():
                raise HTTPException(400, "Choose an active outlet")
            cur.execute("INSERT INTO users (name, role, phone, pin, outlet_id) VALUES (?, ?, ?, ?, ?)",
                        (name.strip(), role, phone.strip(), hash_password(pin), outlet_id))
            audit(cur, user, "CREATE", "USER", cur.lastrowid, role)
            conn.commit()
    except sqlite3.IntegrityError:
        return RedirectResponse("/admin/dashboard?error=Login+ID+already+exists#network", status_code=303)
    return RedirectResponse("/admin/dashboard?message=Staff+user+created#network", status_code=303)


@router.post("/admin/commissions")
def create_commission_rule(
    request: Request,
    scope: str = Form(...), department: str = Form(""), product_id: int | None = Form(None),
    commission_type: str = Form("PERCENT"), commission_value: float = Form(...),
    starts_on: str = Form(""), ends_on: str = Form(""),
) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    operations.validate_dates(starts_on, ends_on)
    if not math.isfinite(commission_value) or (commission_type == "PERCENT" and commission_value > 100):
        raise HTTPException(400, "Invalid commission amount")
    scope = "PRODUCT" if scope == "PRODUCT" else "DEPARTMENT"
    if commission_value < 0 or (scope == "PRODUCT" and not product_id) or (scope == "DEPARTMENT" and not department.strip()):
        return RedirectResponse("/admin/dashboard?error=Complete+the+commission+rule#rules", status_code=303)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO commission_rules (scope, department, product_id, commission_type, commission_value, created_at, starts_on, ends_on) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (scope, department.strip() or None, product_id if scope == "PRODUCT" else None,
                     "FIXED" if commission_type == "FIXED" else "PERCENT", commission_value, now_str(), starts_on, ends_on))
        audit(cur, user, "CREATE", "COMMISSION_RULE", cur.lastrowid, f"{scope} {commission_value}")
        conn.commit()
    return RedirectResponse("/admin/dashboard?message=Commission+rule+saved#rules", status_code=303)


@router.post("/admin/orders/{order_id}/status")
def admin_order_status(order_id: int, request: Request, status: str = Form(...), reason: str = Form("")) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    with get_conn() as conn:
        cur = conn.cursor()
        order = cur.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            return RedirectResponse("/admin/dashboard?error=Order+not+found#overview", status_code=303)
        if status == "EXECUTED" and order["status"] == "ON_HOLD":
            franchise = cur.execute("SELECT wallet_balance, status FROM franchisees WHERE id=?", (order["franchisee_id"],)).fetchone()
            dispatch_rules.validate_mode(cur, order["franchisee_id"], order["fulfillment_mode"])
            debit = float(order["wallet_required"])
            if not franchise or franchise["status"] != "ACTIVE" or float(franchise["wallet_balance"]) < debit:
                return RedirectResponse("/admin/dashboard?error=Insufficient+wallet+or+inactive+franchise#overview", status_code=303)
            operations.reserve_order_inventory(cur, order_id, order["outlet_id"], user["id"])
            wallet_entry(cur, order["franchisee_id"], "ORDER_DEBIT", -debit, "ORDER", order_id, "Order executed by admin", user["id"])
            cur.execute("UPDATE orders SET status='EXECUTED', wallet_debit_amount=?, executed_at=?, updated_at=? WHERE id=?", (debit, now_str(), now_str(), order_id))
        elif status == "CANCELLED" and order["status"] in {"ON_HOLD", "EXECUTED"}:
            if cur.execute("SELECT 1 FROM bills WHERE order_id=?", (order_id,)).fetchone():
                return RedirectResponse("/admin/dashboard?error=Billed+orders+cannot+be+cancelled#overview", status_code=303)
            if order["status"] == "EXECUTED" and float(order["wallet_debit_amount"] or 0) > 0:
                wallet_entry(cur, order["franchisee_id"], "ORDER_REFUND", float(order["wallet_debit_amount"]), "ORDER", order_id, reason or "Order cancelled", user["id"])
            if order["status"] == "EXECUTED":
                operations.release_order_inventory(cur, order_id, order["outlet_id"], user["id"], reason or "Order cancelled")
            cur.execute("UPDATE orders SET status='CANCELLED', updated_at=? WHERE id=?", (now_str(), order_id))
        else:
            return RedirectResponse("/admin/dashboard?error=Invalid+order+transition#overview", status_code=303)
        add_order_event(cur, order_id, status, reason or f"Status changed by admin", user["id"])
        audit(cur, user, "STATUS_CHANGE", "ORDER", order_id, f"{order['status']} -> {status}: {reason}")
        refresh_target_progress(cur, order["franchisee_id"], user["id"])
        conn.commit()
    return RedirectResponse("/admin/dashboard?message=Order+updated#overview", status_code=303)


@router.post("/admin/orders/{order_id}/returns")
def record_order_return(
    order_id: int,
    request: Request,
    order_item_id: int = Form(...),
    quantity: int = Form(...),
    reason: str = Form(..., min_length=4, max_length=500),
    request_key: str = Form(...),
) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    with get_conn() as conn:
        cur = conn.cursor()
        if not operations.claim_mutation(cur, user, "record_order_return", request_key):
            return operations.redirect("This return request was already recorded.")
        order = cur.execute(
            "SELECT status, franchisee_id, commission_available_at, commission_released_at FROM orders WHERE id=?",
            (order_id,),
        ).fetchone()
        item = cur.execute("SELECT id, qty, line_commission FROM order_items WHERE id=? AND order_id=?", (order_item_id, order_id)).fetchone()
        if not order or order["status"] != "DELIVERED" or not item:
            return RedirectResponse("/admin/dashboard?error=Return+requires+a+delivered+order#overview", status_code=303)
        if not order["commission_available_at"] or now_str() >= order["commission_available_at"] or order["commission_released_at"]:
            return RedirectResponse("/admin/dashboard?error=Customer+return+window+has+closed#overview", status_code=303)
        prior = cur.execute(
            "SELECT COALESCE(SUM(quantity),0) qty, COALESCE(SUM(commission_reversed),0) commission FROM order_returns WHERE order_item_id=?",
            (order_item_id,),
        ).fetchone()
        available_qty = int(item["qty"]) - int(prior["qty"] or 0)
        if quantity <= 0 or quantity > available_qty:
            return RedirectResponse("/admin/dashboard?error=Return+quantity+exceeds+remaining+units#overview", status_code=303)
        if quantity == available_qty:
            commission_reversed = round(float(item["line_commission"]) - float(prior["commission"] or 0), 2)
        else:
            commission_reversed = round(float(item["line_commission"]) * quantity / int(item["qty"]), 2)
        cur.execute(
            "INSERT INTO order_returns(order_id,order_item_id,quantity,commission_reversed,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
            (order_id, order_item_id, quantity, commission_reversed, reason.strip(), user["id"], now_str()),
        )
        refresh_target_progress(cur, order["franchisee_id"], user["id"])
        add_order_event(cur, order_id, "RETURN_RECORDED", f"Returned {quantity} unit(s); pending commission adjusted", user["id"])
        audit(cur, user, "RECORD_RETURN", "ORDER_ITEM", order_item_id,
              f"Order {order_id}; qty {quantity}; held commission reduced {commission_reversed:.2f}; {reason.strip()}")
        conn.commit()
    return RedirectResponse("/admin/dashboard?message=Return+recorded+and+held+commission+adjusted#overview", status_code=303)


@router.post("/admin/promotions")
def create_promotion(
    request: Request,
    name: str = Form(...), scope: str = Form(...), department: str = Form(""), product_id: int | None = Form(None),
    franchisee_id: int | None = Form(None),
    discount_type: str = Form("PERCENT"), discount_value: float = Form(...), starts_on: str = Form(""), ends_on: str = Form(""),
) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    operations.validate_dates(starts_on, ends_on)
    if not math.isfinite(discount_value) or discount_type not in {"PERCENT", "FIXED"} or (discount_type == "PERCENT" and discount_value > 100):
        raise HTTPException(400, "Invalid promotion discount")
    scope = "PRODUCT" if scope == "PRODUCT" else "DEPARTMENT"
    if discount_value <= 0 or (scope == "PRODUCT" and not product_id) or (scope == "DEPARTMENT" and not department.strip()):
        return RedirectResponse("/admin/dashboard?error=Complete+the+promotion+rule#rules", status_code=303)
    if starts_on and ends_on and starts_on > ends_on:
        return RedirectResponse("/admin/dashboard?error=Promotion+end+date+must+follow+start+date#rules", status_code=303)
    with get_conn() as conn:
        cur = conn.cursor()
        if franchisee_id and not cur.execute("SELECT 1 FROM franchisees WHERE id=?", (franchisee_id,)).fetchone():
            raise HTTPException(404, "Franchise not found")
        cur.execute(
            "INSERT INTO promotions (name, scope, department, product_id, discount_type, discount_value, starts_on, ends_on, created_at, franchisee_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name.strip(), scope, department.strip() or None, product_id if scope == "PRODUCT" else None,
             "FIXED" if discount_type == "FIXED" else "PERCENT", discount_value, starts_on or None, ends_on or None, now_str(), franchisee_id),
        )
        audit(cur, user, "CREATE", "PROMOTION", cur.lastrowid, name)
        conn.commit()
    return RedirectResponse("/admin/dashboard?message=Promotion+saved#rules", status_code=303)
