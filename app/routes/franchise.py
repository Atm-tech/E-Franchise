"""Franchise HTTP routes."""
from fastapi import APIRouter

# Shared application services stay in app.main during the route extraction.
from app.core import *  # noqa: F401,F403

router = APIRouter()


@router.post("/franchise/demo-topups")
def franchise_demo_topup(request: Request, amount: float = Form(...), request_key: str = Form(..., min_length=16, max_length=64)):
    if os.getenv("APP_ENV", "").lower() == "production" or os.getenv("DATABASE_URL", "").strip():
        raise HTTPException(403, "Demo top-up is disabled")
    user = require_role(request, "FRANCHISEE")
    if isinstance(user, RedirectResponse):
        raise HTTPException(401, "Please sign in again")
    if not instant_demo_topup_available():
        raise HTTPException(403, "Demo top-up is disabled")
    if not math.isfinite(amount) or not 1 <= amount <= 100000:
        raise HTTPException(400, "Enter an amount between INR 1 and 100000")
    with get_conn() as conn:
        cur = conn.cursor()
        franchise = cur.execute("SELECT status,wallet_balance FROM franchisees WHERE id=?", (user["franchisee_id"],)).fetchone()
        if not franchise or franchise["status"] != "ACTIVE":
            raise HTTPException(403, "Franchise account must be active")
        if operations.claim_mutation(cur, user, "franchise_demo_topup", request_key):
            balance = wallet_entry(cur, user["franchisee_id"], "TEST_CREDIT", round(amount, 2), "TEST_MODE", None, "Local demo instant top-up (no real payment)", user["id"])
            audit(cur, user, "TEST_CREDIT", "WALLET", user["franchisee_id"], f"Local demo top-up: {amount:.2f}")
        else:
            balance = cur.execute("SELECT wallet_balance FROM franchisees WHERE id=?", (user["franchisee_id"],)).fetchone()["wallet_balance"]
    return {"status": "CREDITED", "wallet_balance": balance, "demo": True}


@router.get("/franchise/home", response_class=HTMLResponse)
def franchise_home(request: Request, period: str = "month", date_from: str = "", date_to: str = ""):
    user = require_role(request, "FRANCHISEE")
    if isinstance(user, RedirectResponse):
        return user
    with get_conn() as conn:
        cur = conn.cursor()
        release_matured_commissions(cur, user["franchisee_id"])
        refresh_target_progress(cur, user["franchisee_id"], user["id"])
        conn.commit()
        franchise = conn.execute("SELECT * FROM franchisees WHERE id=?", (user["franchisee_id"],)).fetchone()
        product_rows = conn.execute("SELECT * FROM products WHERE status='ACTIVE' ORDER BY id DESC").fetchall()
        products = []
        for row in product_rows:
            sale_price, deal_name = resolve_deal(conn.cursor(), row, float(row["rsp"]), user["franchisee_id"])
            stock_row = conn.execute("SELECT on_hand-reserved available FROM inventory_balances WHERE outlet_id=? AND product_id=?", (user["outlet_id"] or 1,row["id"])).fetchone()
            products.append(dict(row) | {
                "sale_price": sale_price,
                "deal_name": deal_name,
                "commission_per_unit": round(resolve_commission(conn.cursor(), row, sale_price), 2),
                "base_weight_kg": to_kg(float(row["unit_weight"]), row["weight_unit"]) * int(row["pack_multiplier"]),
                "available_stock": int(stock_row["available"]) if stock_row else 0,
            })
        power_pick_ids = {
            product["id"] for product in sorted(products, key=lambda item: float(item["commission_per_unit"]), reverse=True)[:3]
        }
        for product in products:
            product["is_power_pick"] = product["id"] in power_pick_ids
        on_hold_count = conn.execute("SELECT COUNT(*) FROM orders WHERE franchisee_id=? AND status='ON_HOLD'", (user["franchisee_id"],)).fetchone()[0]
        orders = conn.execute(
            """SELECT o.*, b.id bill_id, b.bill_number,
               (SELECT MAX(e.created_at) FROM order_events e WHERE e.order_id=o.id AND e.status='CUSTOMER_DELIVERED') customer_delivered_at FROM orders o
               LEFT JOIN bills b ON b.order_id=o.id
               WHERE o.franchisee_id=? ORDER BY o.created_at ASC, o.id ASC""", (user["franchisee_id"],)
        ).fetchall()
        order_ids = [row["id"] for row in orders]
        reorder_orders = {}
        if order_ids:
            reorder_lines = conn.execute(f"SELECT order_id,product_id,qty,selected_weight_kg FROM order_items WHERE order_id IN ({','.join('?' for _ in order_ids)}) ORDER BY id", tuple(order_ids)).fetchall()
            for order in orders:
                reorder_orders[str(order['id'])] = {'name': order['customer_name'], 'phone': order['customer_phone'], 'mode': order['fulfillment_mode'], 'address': order['buyer_address'], 'items': []}
            for line in reorder_lines:
                reorder_orders[str(line['order_id'])]['items'].append({'id': str(line['product_id']), 'qty': line['qty'], 'weight': line['selected_weight_kg']})

        order_timelines: dict[int, list[dict[str, str]]] = {order_id: [] for order_id in order_ids}
        if order_ids:
            event_rows = conn.execute(
                f"""SELECT order_id, status, notes, created_at FROM order_events
                    WHERE order_id IN ({','.join('?' for _ in order_ids)})
                    ORDER BY created_at, id""",
                order_ids,
            ).fetchall()
            event_titles = {
                "ON_HOLD": "Waiting for wallet balance",
                "EXECUTED": "Order confirmed and sent to outlet",
                "BILLED": "Outlet bill created",
                "PACKED_INDIVIDUALLY": "Order packed at outlet",
                "DOCKET_CREATED": "Grouped into a dispatch docket",
                "HANDED_TO_GUARD": "Sent to guard desk",
                "AT_GUARD_DESK": "Received by guard",
                "SEAL_CHECK": "Seal inspection recorded",
                "WEIGHT_CHECK_PASSED": "Weight check passed",
                "WEIGHT_CHECK_ALERT": "Weight needs recheck",
                "PICKED_BY_FRANCHISEE": "Collected by franchise - customer handover pending",
                "PICKED_BY_DELIVERY": "Handed to delivery partner",
                "DELIVERY_ASSIGNED": "Awaiting delivery partner pickup",
                "ACCEPTED": "Delivery partner accepted the assignment",
                "PICKED_UP": "Delivery partner collected the parcel",
                "OUT_FOR_DELIVERY": "Delivery partner is on the way",
                "ASSIGNED": "Delivery partner assigned",
                "DELIVERED": "Collected by franchise - customer handover pending",
                "CUSTOMER_DELIVERED": "Delivered to customer",
                "RETURN_RECORDED": "Customer return recorded",
                "DELIVERY_FAILED": "Delivery issue reported",
                "CANCELLED": "Order cancelled",
                "REOPEN": "Delivery reopened by admin",
                "REASSIGN": "Delivery reassigned by admin",
                "FORCE_CLOSE": "Delivery closed by admin",
            }
            for event in event_rows:
                order_timelines[event["order_id"]].append({
                    "title": event_titles.get(event["status"], event["status"].replace("_", " ").title()),
                    "status": event["status"],
                    "notes": (event["notes"] or "").strip(),
                    "created_at": event["created_at"] or "",
                })
        journey_seal_required = get_setting(conn.cursor(), 'require_seal_check', '1') == '1'
        journey_docket_required = get_setting(conn.cursor(), 'docket_policy', 'OPTIONAL') in {'REQUIRED', 'AUTO'}
        order_journeys = {order['id']: build_order_journey(order, order_timelines[order['id']],
            require_seal=journey_seal_required, require_docket=journey_docket_required)
            for order in orders}
        dockets = conn.execute(
            "SELECT * FROM dockets WHERE franchisee_id=? ORDER BY id DESC LIMIT 10", (user["franchisee_id"],)
        ).fetchall()
        outlet = conn.execute("SELECT * FROM outlets WHERE id=?", (franchise["outlet_id"],)).fetchone()
        earnings = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) total FROM earnings_wallet_ledger WHERE franchisee_id=? AND entry_type='ORDER_COMMISSION'",
            (user["franchisee_id"],),
        ).fetchone()["total"]
        settings = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings").fetchall()}
        departments = conn.execute("SELECT DISTINCT department FROM products WHERE status='ACTIVE' ORDER BY department").fetchall()
        free_deliveries_used = conn.execute(
            "SELECT COUNT(1) c FROM orders WHERE franchisee_id=? AND fulfillment_mode='DELIVERY_MAN' AND delivery_cost=0 AND status NOT IN ('CANCELLED','ON_HOLD') AND substr(created_at,1,10)=?",
            (user["franchisee_id"], now_str()[:10]),
        ).fetchone()["c"]
        wallet_ledger = conn.execute(
            "SELECT * FROM wallet_ledger WHERE franchisee_id=? ORDER BY id DESC LIMIT 30", (user["franchisee_id"],)
        ).fetchall()
        earnings_wallet = conn.execute(
            "SELECT * FROM earnings_wallets WHERE franchisee_id=?", (user["franchisee_id"],)
        ).fetchone()
        locked_commission = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN o.commission_total-COALESCE((
                       SELECT SUM(r.commission_reversed) FROM order_returns r WHERE r.order_id=o.id),0)>0
                       THEN o.commission_total-COALESCE((SELECT SUM(r2.commission_reversed) FROM order_returns r2 WHERE r2.order_id=o.id),0)
                       ELSE 0 END),0) amount
               FROM orders o WHERE o.franchisee_id=? AND o.status='DELIVERED'
                 AND o.commission_released_at IS NULL AND o.commission_available_at>?""",
            (user["franchisee_id"], now_str()),
        ).fetchone()["amount"]
        earnings_wallet_ledger = conn.execute(
            "SELECT * FROM earnings_wallet_ledger WHERE franchisee_id=? ORDER BY id DESC LIMIT 30", (user["franchisee_id"],)
        ).fetchall()
        targets = target_dashboard(conn.cursor(), user["franchisee_id"])
        guide = earning_guide(conn.cursor(), user["franchisee_id"], products, targets)
        summary_start, summary_end, summary_label = report_period(period, date_from, date_to)
        summary = franchise_summary(conn.cursor(), user["franchisee_id"], summary_start, summary_end, summary_label)
        available_modes = dispatch_rules.allowed_modes(cur, franchise)
        bank_account = conn.execute(
            "SELECT * FROM franchise_bank_accounts WHERE franchisee_id=? AND status!='REMOVED' ORDER BY is_primary DESC, id DESC LIMIT 1",
            (user["franchisee_id"],),
        ).fetchone()
        dispatches = conn.execute(
            """
            SELECT 'BILL' entity_type, b.id entity_id, b.bill_number number, b.status, b.expected_weight_kg, b.created_at,
                   o.customer_name, o.order_number,
                   (SELECT t.status FROM delivery_tasks t WHERE t.entity_type='BILL' AND t.entity_id=b.id ORDER BY t.id DESC LIMIT 1) task_status,
                   1 order_count, o.fulfillment_mode,
                   CASE WHEN EXISTS(SELECT 1 FROM docket_bills db WHERE db.bill_id=b.id) THEN 1 ELSE 0 END is_grouped,
                   (SELECT t.id FROM delivery_tasks t WHERE t.entity_type='BILL' AND t.entity_id=b.id ORDER BY t.id DESC LIMIT 1) task_id
            FROM bills b JOIN orders o ON o.id=b.order_id WHERE b.franchisee_id=? AND NOT EXISTS (SELECT 1 FROM docket_bills grouped WHERE grouped.bill_id=b.id)
            UNION ALL
            SELECT 'DOCKET', d.id entity_id, d.docket_number, d.status, d.expected_weight_kg, d.created_at,
                   'Multiple orders', (SELECT group_concat(b.bill_number, ', ') FROM docket_bills db JOIN bills b ON b.id=db.bill_id WHERE db.docket_id=d.id),
                   (SELECT t.status FROM delivery_tasks t WHERE t.entity_type='DOCKET' AND t.entity_id=d.id ORDER BY t.id DESC LIMIT 1),
                   (SELECT COUNT(*) FROM docket_bills db WHERE db.docket_id=d.id),
                   MIN(o.fulfillment_mode), 0,
                   (SELECT t.id FROM delivery_tasks t WHERE t.entity_type='DOCKET' AND t.entity_id=d.id ORDER BY t.id DESC LIMIT 1)
            FROM dockets d
            JOIN docket_bills dispatch_db ON dispatch_db.docket_id=d.id
            JOIN bills dispatch_b ON dispatch_b.id=dispatch_db.bill_id
            JOIN orders o ON o.id=dispatch_b.order_id
            WHERE d.franchisee_id=?
            GROUP BY d.id
            ORDER BY 2 DESC LIMIT 30
            """, (user["franchisee_id"], user["franchisee_id"])
        ).fetchall()
        dispatch_stage_labels = {
            "BILLED": "Bill created",
            "PACKED_INDIVIDUALLY": "Packed at outlet",
            "DOCKET_CREATED": "Grouped for dispatch",
            "HANDED_TO_GUARD": "Sent to guard desk",
            "AT_GUARD_DESK": "At guard desk",
            "WEIGHT_CHECK_ALERT": "Weight needs recheck",
            "WEIGHT_CHECK_PASSED": "Verified by guard",
            "DELIVERY_ASSIGNED": "Delivery partner assigned - awaiting pickup",
            "PICKED_BY_FRANCHISEE": "Picked up by franchise",
            "PICKED_BY_DELIVERY": "Handed to delivery partner",
            "OUT_FOR_DELIVERY": "On the way to franchise",
            "DELIVERED": "Collected at franchise",
        }
        task_stage_labels = {
            "ASSIGNED": "Delivery assigned",
            "ACCEPTED": "Delivery partner accepted",
            "PICKED_UP": "Collected by delivery partner",
            "OUT_FOR_DELIVERY": "On the way to franchise",
            "DELIVERED": "Collected at franchise",
            "FAILED": "Delivery needs attention",
        }
        dispatches = [dict(row) | {
            "status_label": ("Collected from outlet" if row["fulfillment_mode"] == "FRANCHISE_PICKUP" and row["status"] in {"DELIVERED", "PICKED_BY_FRANCHISEE"} else task_stage_labels.get(row["task_status"], dispatch_stage_labels.get(row["status"], row["status"].replace("_", " ").title()))),
            "can_pickup_code": row["fulfillment_mode"] == "FRANCHISE_PICKUP" and row["status"] in {"WEIGHT_CHECK_PASSED", "WEIGHT_CHECK_ALERT"} and (row["entity_type"] == "DOCKET" or not row["is_grouped"]),
            "can_delivery_code": row["fulfillment_mode"] == "DELIVERY_MAN" and row["task_status"] == "OUT_FOR_DELIVERY" and row["task_id"] is not None,
            "dispatch_info": (
                f"{row['order_count']} bills · {row['order_number']}" if row["entity_type"] == "DOCKET"
                else f"Order {row['order_number']} · {row['customer_name']}"
            ),
        } for row in dispatches]
        docket_contents = {}
        docket_ids = [item['entity_id'] for item in dispatches if item['entity_type'] == 'DOCKET']
        if docket_ids:
            children = conn.execute(
                f"""SELECT db.docket_id, b.id bill_id, b.bill_number, o.order_number,
                           o.customer_name, o.customer_phone, o.buyer_address, o.total, o.created_at
                    FROM docket_bills db JOIN bills b ON b.id=db.bill_id
                    JOIN orders o ON o.id=b.order_id JOIN dockets d ON d.id=db.docket_id
                    WHERE db.docket_id IN ({','.join('?' for _ in docket_ids)})
                      AND d.franchisee_id=? AND b.franchisee_id=? AND o.franchisee_id=?
                    ORDER BY o.created_at ASC, o.id ASC, b.id ASC""",
                (*docket_ids, user['franchisee_id'], user['franchisee_id'], user['franchisee_id']),
            ).fetchall()
            for child in children:
                docket_contents.setdefault(child['docket_id'], []).append(dict(child))
        for dispatch in dispatches:
            dispatch['bills'] = docket_contents.get(dispatch['entity_id'], []) if dispatch['entity_type'] == 'DOCKET' else []
        def dispatch_priority(item):
            if item['status'] in {'DELIVERED', 'PICKED_BY_FRANCHISEE'} or item['task_status'] == 'DELIVERED':
                priority = 4
            elif item['task_status'] == 'FAILED' or item['status'] == 'WEIGHT_CHECK_ALERT':
                priority = 0
            elif item['can_pickup_code'] or item['can_delivery_code']:
                priority = 1
            else:
                priority = 3 if item.get('is_grouped') and item['entity_type'] == 'BILL' else 2
            return priority, item['created_at'] or '', item['number']
        dispatches.sort(key=dispatch_priority)
    return templates.TemplateResponse(
        "franchise_home.html",
        {"request": request, "user": user, "franchise": franchise, "products": products, "orders": orders,
         "dockets": dockets, "outlet": outlet, "earnings": earnings, "settings": settings, "departments": departments,
         "order_timelines": order_timelines, "order_journeys": order_journeys, "on_hold_count": on_hold_count, "reorder_orders": reorder_orders,
         "wallet_ledger": wallet_ledger, "earnings_wallet": earnings_wallet, "locked_commission": locked_commission,
         "earnings_wallet_ledger": earnings_wallet_ledger, "targets": targets,
         "guide": guide, "summary": summary, "summary_period": period,
         "bank_account": bank_account, "dispatches": dispatches, "payment_ready": operations.providers.payment_ready(), "demo_topup_ready": instant_demo_topup_available(),
         "allowed_modes": available_modes, "free_deliveries_used": free_deliveries_used},
    )


@router.get("/api/franchise/inventory")
def franchise_inventory(request: Request):
    user = require_role(request, "FRANCHISEE")
    if isinstance(user, RedirectResponse):
        raise HTTPException(401, "Franchise login required")
    outlet_id = user["outlet_id"] or 1
    with get_conn() as conn:
        outlet = conn.execute("SELECT id,code,name FROM outlets WHERE id=?", (outlet_id,)).fetchone()
        rows = conn.execute("SELECT p.id,COALESCE(ib.on_hand-ib.reserved,0) available FROM products p LEFT JOIN inventory_balances ib ON ib.product_id=p.id AND ib.outlet_id=? WHERE p.status='ACTIVE'", (outlet_id,)).fetchall()
    return {"outlet": dict(outlet) if outlet else None, "stock": {str(row["id"]): int(row["available"]) for row in rows}}


@router.get("/franchise/bills/{bill_id}", response_class=HTMLResponse)
def customer_bill(request: Request, bill_id: int):
    user = require_role(request, "FRANCHISEE")
    if isinstance(user, RedirectResponse):
        return user
    with get_conn() as conn:
        bill = conn.execute(
            """SELECT b.*, o.order_number, o.customer_name, o.customer_phone, o.buyer_address, o.item_subtotal,
                      o.delivery_charge, o.total, o.fulfillment_mode, o.supply_state_code, o.supplier_state_code, o.created_at order_created_at,
                      o.return_window_days, o.delivered_at, o.commission_available_at,
                      f.shop_name, f.name franchise_name, f.mobile franchise_phone, f.address franchise_address, f.gst_number franchise_gstin,
                      ot.name outlet_name, ot.code outlet_code, ot.address outlet_address
               FROM bills b JOIN orders o ON o.id=b.order_id JOIN franchisees f ON f.id=b.franchisee_id
               JOIN outlets ot ON ot.id=b.outlet_id
               WHERE b.id=? AND b.franchisee_id=?""", (bill_id, user["franchisee_id"]),
        ).fetchone()
        if not bill:
            return RedirectResponse("/franchise/home", status_code=303)
        item_rows = conn.execute(
            """SELECT oi.*, COALESCE(NULLIF(oi.product_name_snapshot,''),p.product_name) product_name,
                      COALESCE(NULLIF(oi.hsn_code,''),p.hsn_code) display_hsn
               FROM order_items oi LEFT JOIN products p ON p.id=oi.product_id WHERE oi.order_id=? ORDER BY oi.id""",
            (bill["order_id"],),
        ).fetchall()
        items = [dict(row) for row in item_rows]
        tax_breakdown = invoice_tax_breakdown(items, float(bill["delivery_charge"] or 0), bill["supply_state_code"] != bill["supplier_state_code"])
        settings = {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM settings").fetchall()}
    return_by = bill["commission_available_at"]
    return_policy = f"Customer returns are accepted within {bill['return_window_days']} days after confirmed pickup/delivery."
    if return_by:
        return_policy += f" Contact the franchise to record a return before {return_by} IST."
    item_text = ", ".join(
        f"{item['product_name']} {item['pack_label'] or ('%g kg' % item['selected_weight_kg'])} x {item['qty']}"
        for item in items
    )
    total_tax = sum(row["gst"] for row in tax_breakdown)
    message = (
        f"Aapoorti Tax Invoice {bill['bill_number']}\nCustomer: {bill['customer_name']}\n"
        f"Items: {item_text}\nGST-inclusive total: INR {bill['total']:.2f}\n"
        f"Included GST: INR {total_tax:.2f}\n{return_policy}\nThank you for shopping with {bill['shop_name']}."
    )
    phone = "".join(char for char in bill["customer_phone"] if char.isdigit())
    if len(phone) == 10:
        phone = "91" + phone
    whatsapp_url = f"https://wa.me/{phone}?text={quote(message)}"
    return templates.TemplateResponse(
        "customer_bill.html",
        {"request": request, "user": user, "bill": bill, "items": items, "tax_breakdown": tax_breakdown,
         "total_tax": round(total_tax, 2),
         "delivery_taxable": round(float(bill["delivery_charge"] or 0) * 100 / 118, 2),
         "settings": settings, "whatsapp_url": whatsapp_url, "return_policy": return_policy},
    )


@router.get("/franchise/summary.pdf")
def download_franchise_summary(
    request: Request, period: str = "month", date_from: str = "", date_to: str = "",
) -> Response:
    user = require_role(request, "FRANCHISEE")
    if isinstance(user, RedirectResponse):
        return user
    start, end, label = report_period(period, date_from, date_to)
    with get_conn() as conn:
        summary = franchise_summary(conn.cursor(), user["franchisee_id"], start, end, label)
        franchise = conn.execute("SELECT * FROM franchisees WHERE id=?", (user["franchisee_id"],)).fetchone()
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=16*mm, leftMargin=16*mm, topMargin=16*mm, bottomMargin=16*mm)
    styles = getSampleStyleSheet()
    story = [Paragraph("Aapoorti Partner Summary", styles["Title"]), Paragraph(franchise["shop_name"], styles["Heading2"]),
             Paragraph(f"{summary['label']} | IST | Generated {now_str()}", styles["BodyText"]), Spacer(1, 7*mm)]
    rows = [
        ["Metric", "Amount / Count"], ["GST-inclusive sales", f"INR {summary['sales']:.2f}"],
        ["Target-eligible sales", f"INR {summary['eligible_sales']:.2f}"], ["Executed orders", str(summary["orders"])],
        ["Unique customers", str(summary["customers"])], ["Average basket", f"INR {summary['average_basket']:.2f}"],
        ["Product profit", f"INR {summary['commission']:.2f}"], ["Target rewards credited", f"INR {summary['target_rewards']:.2f}"],
        ["Purchase-wallet deposits", f"INR {summary['wallet_in']:.2f}"], ["Purchase-wallet withdrawals", f"INR {summary['wallet_out']:.2f}"],
        ["Earnings transferred", f"INR {summary['earnings_transferred']:.2f}"], ["Included GST", f"INR {summary['total_tax']:.2f}"],
    ]
    table = Table(rows, colWidths=[105*mm, 55*mm])
    table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#0b4f3c")),("TEXTCOLOR",(0,0),(-1,0),colors.white),
                               ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("GRID",(0,0),(-1,-1),.35,colors.HexColor("#ccd8d1")),
                               ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f3f7f4")]),("PADDING",(0,0),(-1,-1),7)]))
    story.extend([table, Spacer(1, 7*mm), Paragraph("GST rate-wise breakup", styles["Heading2"])])
    tax_rows = [["Rate", "Taxable", "CGST", "SGST", "IGST", "Total GST", "Gross"]]
    tax_rows.extend([[f"{row['gst_rate']:g}%", f"{row['taxable']:.2f}", f"{row['cgst']:.2f}", f"{row['sgst']:.2f}", f"{row['igst']:.2f}", f"{row['tax']:.2f}", f"{row['gross']:.2f}"] for row in summary["tax_breakdown"]])
    tax_table = Table(tax_rows, repeatRows=1)
    tax_table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#dff0e5")),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
                                   ("GRID",(0,0),(-1,-1),.35,colors.HexColor("#ccd8d1")),("ALIGN",(1,1),(-1,-1),"RIGHT"),("PADDING",(0,0),(-1,-1),6)]))
    story.append(tax_table)
    story.extend([Spacer(1, 7*mm), Paragraph("Figures include executed orders only. On-hold and cancelled orders are excluded. Product rates are GST-inclusive.", styles["BodyText"])])
    document.build(story)
    filename = f"aapoorti-summary-{period}-{now_str()[:10]}.pdf"
    return Response(buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/franchise/earnings/transfer")
def transfer_earnings_to_purchase_wallet(request: Request, amount: float = Form(...), request_key: str = Form(...)) -> RedirectResponse:
    user = require_role(request, "FRANCHISEE")
    if isinstance(user, RedirectResponse):
        return user
    amount = round(amount, 2)
    if amount <= 0:
        return RedirectResponse("/franchise/home?error=earnings_wallet#account", status_code=303)
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            release_matured_commissions(cur, user["franchisee_id"])
            if not operations.claim_mutation(cur, user, "transfer_earnings_to_purchase_wallet", request_key):
                return operations.redirect("This request was already processed. Review the ledger.")
            wallet = cur.execute("SELECT balance FROM earnings_wallets WHERE franchisee_id=?", (user["franchisee_id"],)).fetchone()
            if not wallet or amount > float(wallet["balance"]):
                raise ValueError("Insufficient earnings wallet balance")
            cur.execute(
                """INSERT INTO wallet_transfers
                   (franchisee_id, amount, source_wallet, destination_wallet, status, created_by, created_at)
                   VALUES (?, ?, 'EARNINGS', 'PURCHASE', 'PROCESSING', ?, ?)""",
                (user["franchisee_id"], amount, user["id"], now_str()),
            )
            transfer_id = cur.lastrowid
            earnings_balance = earnings_wallet_entry(
                cur, user["franchisee_id"], "TRANSFER_OUT", -amount, "WALLET_TRANSFER", transfer_id,
                "Transferred to purchase wallet", user["id"],
            )
            purchase_balance = wallet_entry(
                cur, user["franchisee_id"], "EARNINGS_TRANSFER_IN", amount, "WALLET_TRANSFER", transfer_id,
                "Transferred from earnings wallet", user["id"],
            )
            cur.execute("UPDATE wallet_transfers SET status='COMPLETED' WHERE id=?", (transfer_id,))
            audit(cur, user, "WALLET_TRANSFER", "FRANCHISEE", user["franchisee_id"],
                  f"Earnings to purchase: {amount:.2f}; earnings {earnings_balance:.2f}; purchase {purchase_balance:.2f}")
            conn.commit()
    except ValueError:
        return RedirectResponse("/franchise/home?error=earnings_wallet#account", status_code=303)
    return RedirectResponse("/franchise/home?message=earnings_transferred#account", status_code=303)


@router.get("/franchise/customers/list")
def list_franchise_customers(request: Request, q: str = '', offset: int = 0):
    user = require_role(request, "FRANCHISEE")
    if isinstance(user, RedirectResponse):
        raise HTTPException(401, "Please sign in again")
    offset = max(0, offset)
    with get_conn() as conn:
        rows = conn.execute("""WITH phones AS (
            SELECT customer_phone phone FROM orders WHERE franchisee_id=? AND customer_phone<>''
            UNION SELECT phone FROM franchise_customer_profiles WHERE franchisee_id=?
        ), directory AS (
            SELECT phones.phone, COALESCE(profile.name,(SELECT customer_name FROM orders WHERE franchisee_id=? AND customer_phone=phones.phone ORDER BY id DESC LIMIT 1)) name
            FROM phones LEFT JOIN franchise_customer_profiles profile ON profile.phone=phones.phone AND profile.franchisee_id=?
        ) SELECT phone,name FROM directory WHERE LOWER(name) LIKE ? OR phone LIKE ?
        ORDER BY LOWER(name),phone LIMIT 21 OFFSET ?""", (user['franchisee_id'],user['franchisee_id'],user['franchisee_id'],user['franchisee_id'],'%'+q.strip().lower()[:100]+'%','%'+q.strip()[:100]+'%',offset)).fetchall()
    return {'customers': [dict(row) for row in rows[:20]], 'has_more': len(rows)>20, 'next_offset':offset+min(20,len(rows))}


@router.get("/franchise/customers/{phone}/details")
def franchise_customer_details(request: Request, phone: str):
    user = require_role(request, "FRANCHISEE")
    if isinstance(user, RedirectResponse):
        raise HTTPException(401, "Please sign in again")
    with get_conn() as conn:
        profile = conn.execute('SELECT * FROM franchise_customer_profiles WHERE franchisee_id=? AND phone=?', (user['franchisee_id'],phone)).fetchone()
        metrics = conn.execute("""SELECT COUNT(*) orders,
            SUM(CASE WHEN status IN ('DELIVERED','PICKED_BY_FRANCHISEE') THEN 1 ELSE 0 END) completed,
            SUM(CASE WHEN status IN ('DELIVERED','PICKED_BY_FRANCHISEE') THEN total ELSE 0 END) spent,
            MIN(CASE WHEN status IN ('DELIVERED','PICKED_BY_FRANCHISEE') THEN created_at END) first_purchase,
            MAX(CASE WHEN status IN ('DELIVERED','PICKED_BY_FRANCHISEE') THEN created_at END) last_purchase
            FROM orders WHERE franchisee_id=? AND customer_phone=?""",(user['franchisee_id'],phone)).fetchone()
        latest = conn.execute('SELECT id,customer_name,created_at FROM orders WHERE franchisee_id=? AND customer_phone=? ORDER BY id DESC LIMIT 1',(user['franchisee_id'],phone)).fetchone()
        if not profile and not latest:
            raise HTTPException(404, 'Customer not found')
        products = conn.execute("""SELECT COALESCE(NULLIF(oi.product_name_snapshot,''),p.product_name) name, COUNT(DISTINCT o.id) purchases
            FROM orders o JOIN order_items oi ON oi.order_id=o.id LEFT JOIN products p ON p.id=oi.product_id
            WHERE o.franchisee_id=? AND o.customer_phone=? AND o.status IN ('DELIVERED','PICKED_BY_FRANCHISEE')
            GROUP BY oi.product_id,COALESCE(NULLIF(oi.product_name_snapshot,''),p.product_name)
            ORDER BY purchases DESC LIMIT 3""",(user['franchisee_id'],phone)).fetchall()
    completed = int(metrics['completed'] or 0)
    interval = None
    if completed>1:
        interval=round((datetime.strptime(metrics['last_purchase'],'%Y-%m-%d %H:%M:%S')-datetime.strptime(metrics['first_purchase'],'%Y-%m-%d %H:%M:%S')).total_seconds()/86400/(completed-1))
    return {'name':profile['name'] if profile else latest['customer_name'],'phone':phone,'notes':profile['notes'] if profile else '', 'address':profile['address'] if profile else '', 'orders':metrics['orders'],'completed':completed,'spent':float(metrics['spent'] or 0),'average':float(metrics['spent'] or 0)/completed if completed else 0,'last_purchase':metrics['last_purchase'],'interval':interval,'repeat_id':latest['id'] if latest else None,'products':[dict(row) for row in products]}


@router.post("/franchise/customers")
def save_franchise_customer(request: Request, name: str = Form(...), phone: str = Form(...), notes: str = Form(''), address: str = Form('')):
    user = require_role(request, "FRANCHISEE")
    if isinstance(user, RedirectResponse):
        return user
    name, phone, notes, address = name.strip(), phone.strip(), notes.strip(), address.strip()
    if not 2 <= len(name) <= 100 or len(phone) != 10 or not phone.isascii() or not phone.isdigit() or len(notes) > 1000 or len(address) > 500:
        raise HTTPException(400, "Enter a name, 10-digit mobile number and notes up to 1000 characters")
    with get_conn() as conn:
        conn.execute("""INSERT INTO franchise_customer_profiles(franchisee_id,phone,name,notes,address,updated_at) VALUES(?,?,?,?,?,?)
            ON CONFLICT(franchisee_id,phone) DO UPDATE SET name=excluded.name,notes=excluded.notes,address=excluded.address,updated_at=excluded.updated_at""", (user['franchisee_id'], phone, name, notes, address, now_str()))
        audit(conn.cursor(), user, "SAVE_CUSTOMER", "CUSTOMER", None, phone)
    return RedirectResponse('/franchise/home?message=customer_saved#customers', status_code=303)


@router.post("/franchise/orders/{order_id}/cancel")
def cancel_held_order(order_id: int, request: Request, request_key: str = Form(..., min_length=16, max_length=64)):
    user = require_role(request, "FRANCHISEE")
    if isinstance(user, RedirectResponse):
        return user
    with get_conn() as conn:
        cur = conn.cursor()
        order = cur.execute("SELECT * FROM orders WHERE id=? AND franchisee_id=?", (order_id, user["franchisee_id"])).fetchone()
        if not order:
            raise HTTPException(404, "Order not found")
        if order["status"] == "CANCELLED":
            return RedirectResponse("/franchise/home?message=order_cancelled#orders", status_code=303)
        if order["status"] != "ON_HOLD" or cur.execute("SELECT 1 FROM bills WHERE order_id=?", (order_id,)).fetchone():
            raise HTTPException(409, "Only unbilled on-hold orders can be cancelled here")
        if operations.claim_mutation(cur, user, "cancel_held_order", request_key):
            cur.execute("UPDATE orders SET status='CANCELLED',updated_at=? WHERE id=? AND status='ON_HOLD'", (now_str(), order_id))
            if cur.rowcount != 1:
                raise HTTPException(409, "Order changed. Refresh and try again")
            add_order_event(cur, order_id, "CANCELLED", "On-hold order cancelled by franchisee", user["id"])
            audit(cur, user, "CANCEL", "ORDER", order_id, order["order_number"])
    return RedirectResponse("/franchise/home?message=order_cancelled#orders", status_code=303)


@router.post("/franchise/orders/{order_id}/customer-handover")
def customer_handover(order_id: int, request: Request, request_key: str = Form(..., min_length=16, max_length=64)):
    user = require_role(request, "FRANCHISEE")
    if isinstance(user, RedirectResponse):
        return user
    with get_conn() as conn:
        cur = conn.cursor()
        # Lock the owned order before checking the event, including on PostgreSQL.
        cur.execute("UPDATE orders SET updated_at=updated_at WHERE id=? AND franchisee_id=?",
                    (order_id, user["franchisee_id"]))
        order = cur.execute("SELECT * FROM orders WHERE id=? AND franchisee_id=?",
                            (order_id, user["franchisee_id"])).fetchone()
        if not order:
            raise HTTPException(404, "Order not found")
        if order["status"] not in {"DELIVERED", "PICKED_BY_FRANCHISEE"}:
            raise HTTPException(409, "Collect the order before customer handover")
        if not cur.execute("SELECT 1 FROM order_events WHERE order_id=? AND status='CUSTOMER_DELIVERED'", (order_id,)).fetchone():
            if operations.claim_mutation(cur, user, "customer_handover", request_key):
                add_order_event(cur, order_id, "CUSTOMER_DELIVERED", "Franchise confirmed customer handover", user["id"])
                audit(cur, user, "CUSTOMER_HANDOVER", "ORDER", order_id, order["order_number"])
    return RedirectResponse("/franchise/home?message=customer_delivered#orders", status_code=303)


@router.post("/franchise/orders/{order_id}/execute")
def execute_held_order(order_id: int, request: Request) -> RedirectResponse:
    user = require_role(request, "FRANCHISEE")
    if isinstance(user, RedirectResponse):
        return user
    with get_conn() as conn:
        cur = conn.cursor()
        order = cur.execute("SELECT * FROM orders WHERE id=? AND franchisee_id=?", (order_id, user["franchisee_id"])).fetchone()
        franchise = cur.execute("SELECT * FROM franchisees WHERE id=?", (user["franchisee_id"],)).fetchone()
        if not order or order["status"] != "ON_HOLD":
            return RedirectResponse("/franchise/home?error=invalid_order#orders", status_code=303)
        if not franchise or franchise["status"] != "ACTIVE" or float(franchise["wallet_balance"]) < float(order["wallet_required"]):
            return RedirectResponse("/franchise/home?error=wallet#orders", status_code=303)
        dispatch_rules.validate_mode(cur, franchise["id"], order["fulfillment_mode"])
        operations.reserve_order_inventory(cur, order_id, order["outlet_id"], user["id"])
        wallet_entry(cur, franchise["id"], "ORDER_DEBIT", -float(order["wallet_required"]), "ORDER", order_id, order["order_number"], user["id"])
        cur.execute("UPDATE orders SET status='EXECUTED', wallet_debit_amount=?, executed_at=?, updated_at=? WHERE id=?", (order["wallet_required"], now_str(), now_str(), order_id))
        add_order_event(cur, order_id, "EXECUTED", "Executed by franchisee", user["id"])
        audit(cur, user, "EXECUTE", "ORDER", order_id, order["order_number"])
        refresh_target_progress(cur, franchise["id"], user["id"])
        conn.commit()
    return RedirectResponse("/franchise/home?message=order_executed#orders", status_code=303)


@router.post("/franchise/orders")
def create_order(
    request: Request,
    product_id: int | None = Form(None),
    qty: int = Form(1),
    delivery_type: str = Form("DAY"),
    fulfillment_mode: str = Form("FRANCHISE_PICKUP"),
    status_mode: str = Form("EXECUTED"),
    cart_json: str = Form(""),
    customer_name: str = Form(""),
    customer_phone: str = Form(""),
    customer_address: str = Form(""),
    buyer_address: str = Form(""),
    distance_km: float = Form(0),
    request_key: str = Form(...),
) -> RedirectResponse:
    user = require_role(request, "FRANCHISEE")
    if isinstance(user, RedirectResponse):
        return user
    buyer_address = buyer_address.strip()
    if len(buyer_address) > 500:
        raise HTTPException(400, "Customer address must be within 500 characters")
    customer_name = customer_name.strip()
    customer_phone = "".join(char for char in customer_phone if char.isdigit())
    if len(customer_name) < 2 or len(customer_phone) != 10:
        return RedirectResponse("/franchise/home?error=customer_details#checkout", status_code=303)
    with get_conn() as conn:
        cur = conn.cursor()
        franchise = cur.execute("SELECT * FROM franchisees WHERE id=?", (user["franchisee_id"],)).fetchone()
        try:
            cart = json.loads(cart_json) if cart_json else [{"id": product_id, "qty": qty}]
        except (json.JSONDecodeError, TypeError):
            cart = []
        try:
            if not isinstance(cart, list) or len(cart) > 200:
                raise ValueError()
            cart = [item for item in cart if isinstance(item, dict) and item.get("id") and 0 < int(item.get("qty", 0)) <= 1000]
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid basket quantities")
        if fulfillment_mode not in {"DELIVERY_MAN", "FRANCHISE_PICKUP"} or delivery_type not in {"DAY", "INSTANT"} or status_mode not in {"EXECUTED", "ON_HOLD"}:
            raise HTTPException(400, "Invalid order options")
        if not math.isfinite(distance_km) or not 0 <= distance_km <= 1000:
            raise HTTPException(400, "Invalid distance")
        if not franchise or not cart:
            return RedirectResponse("/franchise/home#checkout", status_code=303)
        if franchise["status"] != "ACTIVE":
            return RedirectResponse("/franchise/home?error=inactive#checkout", status_code=303)
        dispatch_rules.validate_mode(cur, franchise["id"], fulfillment_mode)
        franchise_delivery_address = (franchise["delivery_address"] or franchise["address"] or "").strip()
        if fulfillment_mode == "DELIVERY_MAN" and len(franchise_delivery_address) < 5:
            return RedirectResponse("/franchise/home?error=delivery_address#checkout", status_code=303)

        try:
            product_ids = [int(item["id"]) for item in cart]
        except (TypeError, ValueError):
            raise HTTPException(400, "Invalid product ID")
        product_rows = cur.execute(
            f"SELECT * FROM products WHERE id IN ({','.join(['?'] * len(product_ids))}) AND status='ACTIVE'",
            tuple(product_ids),
        ).fetchall()
        product_map = {row["id"]: row for row in product_rows}
        lines = []
        item_subtotal = 0.0
        commission_total = 0.0
        for item in cart:
            product = product_map.get(int(item["id"]))
            if not product:
                continue
            item_qty = max(1, int(item["qty"]))
            base_weight_kg = to_kg(float(product["unit_weight"]), product["weight_unit"]) * int(product["pack_multiplier"])
            try:
                selected_weight_kg = float(item.get("weight_kg", base_weight_kg))
            except (TypeError, ValueError):
                continue
            variable_weight = bool(product["variable_weight"])
            if base_weight_kg <= 0 or (variable_weight and selected_weight_kg not in {0.5, 1.0, 2.0, 5.0}):
                continue
            if not variable_weight:
                selected_weight_kg = base_weight_kg
            base_sale_price, _ = resolve_deal(cur, product, float(product["rsp"]), user["franchisee_id"])
            sale_price = round(base_sale_price * selected_weight_kg / base_weight_kg, 2) if variable_weight else round(base_sale_price, 2)
            line_total = round(sale_price * item_qty, 2)
            unit_commission = round(resolve_commission(cur, product, sale_price), 2)
            commission = round(unit_commission * item_qty, 2)
            gst_rate = float(product["gst_rate"] or 0)
            taxable_value = round(line_total * 100 / (100 + gst_rate), 2) if gst_rate else line_total
            gst_amount = round(line_total - taxable_value, 2)
            item_subtotal = round(item_subtotal + line_total, 2)
            commission_total = round(commission_total + commission, 2)
            lines.append((product, item_qty, selected_weight_kg, sale_price, line_total, unit_commission, commission,
                          gst_rate, taxable_value, gst_amount))
        if not lines:
            return RedirectResponse("/franchise/home#checkout", status_code=303)

        if fulfillment_mode == "DELIVERY_MAN" and franchise["route_distance_km"] is not None and franchise["route_outlet_id"] == franchise["outlet_id"]:
            distance_km = float(franchise["route_distance_km"])
        instant_charge = float(get_setting(cur, "instant_premium_charge", "40"))
        day_charge = float(get_setting(cur, "day_delivery_charge", "10"))
        per_km = float(get_setting(cur, "delivery_per_km", "8"))
        delivery_charge = 0.0
        if fulfillment_mode == "DELIVERY_MAN":
            threshold = float(get_setting(cur, "free_delivery_threshold", "999"))
            free_limit = int(float(get_setting(cur, "free_delivery_limit_per_day", "3")))
            today = now_str()[:10]
            free_used = cur.execute(
                "SELECT COUNT(1) c FROM orders WHERE franchisee_id=? AND fulfillment_mode='DELIVERY_MAN' AND delivery_cost=0 AND status NOT IN ('CANCELLED','ON_HOLD') AND substr(created_at,1,10)=?",
                (user["franchisee_id"], today),
            ).fetchone()["c"]
            if item_subtotal >= threshold and free_used < free_limit and delivery_type == "DAY":
                delivery_charge = 0.0
            else:
                delivery_base = float(get_setting(cur, "delivery_fixed_charge", "0")) if get_setting(cur, "delivery_pricing_mode", "PER_KM") == "FIXED" else max(0, distance_km) * per_km
                delivery_charge = delivery_base + (instant_charge if delivery_type == "INSTANT" else day_charge)
        delivery_cost = round(delivery_charge, 2)
        bearer = get_setting(cur, "delivery_charge_bearer", "CUSTOMER")
        delivery_charge = delivery_cost if bearer == "CUSTOMER" else 0.0
        total = round(item_subtotal + delivery_charge, 2)
        payable_to_outlet = round(max(0, item_subtotal - commission_total), 2)
        wallet_required = round(payable_to_outlet + (delivery_cost if bearer != "BUSINESS" else 0), 2)

        final_status = "ON_HOLD" if status_mode == "ON_HOLD" else "EXECUTED"
        if final_status == "EXECUTED" and wallet_required > float(franchise["wallet_balance"]):
            final_status = "ON_HOLD"

        mapped_outlet = cur.execute("SELECT id FROM outlets WHERE id=? AND status='ACTIVE'", (franchise["outlet_id"],)).fetchone()
        if not mapped_outlet:
            return RedirectResponse("/franchise/home?error=mapped_outlet_unavailable#checkout", status_code=303)
        outlet_id = franchise["outlet_id"]
        if final_status == "EXECUTED":
            requested = {}
            for product, item_qty, *_ in lines:
                requested[product["id"]] = requested.get(product["id"], 0) + item_qty
            for pid, needed in requested.items():
                stock = cur.execute("SELECT on_hand-reserved available FROM inventory_balances WHERE outlet_id=? AND product_id=?", (outlet_id,pid)).fetchone()
                available = int(stock["available"]) if stock else 0
                if needed > available:
                    product = product_map[pid]
                    return RedirectResponse(f"/franchise/home?error={quote_plus('Not enough stock for ' + product['product_name'] + ': ' + str(available) + ' available')}#checkout", status_code=303)

        previous = cur.execute("SELECT order_number,status FROM orders WHERE request_key=? AND franchisee_id=?", (request_key, user["franchisee_id"])).fetchone() if request_key else None
        if previous:
            return RedirectResponse(f"/franchise/home?created={previous['order_number']}&status={previous['status']}#orders", status_code=303)
        if final_status == "ON_HOLD":
            requested = {}
            for product, item_qty, *_ in lines:
                requested[product["id"]] = requested.get(product["id"], 0) + item_qty
            for pid, needed in requested.items():
                stock = cur.execute("SELECT on_hand-reserved available FROM inventory_balances WHERE outlet_id=? AND product_id=?", (outlet_id,pid)).fetchone()
                available = int(stock["available"]) if stock else 0
                if needed > available:
                    product = product_map[pid]
                    return RedirectResponse(f"/franchise/home?error={quote_plus('Not enough stock for ' + product['product_name'] + ': ' + str(available) + ' available')}#checkout", status_code=303)
        order_number = next_code("ORD-", "orders", "order_number")
        return_window_days = max(1, min(30, int(float(get_setting(cur, "customer_return_window_days", "4")))))
        created_timestamp = now_str()
        executed_timestamp = created_timestamp if final_status == "EXECUTED" else None
        cur.execute(
            """
            INSERT INTO orders
            (order_number, franchisee_id, outlet_id, delivery_type, fulfillment_mode, status, item_subtotal, commission_total, delivery_charge, total, created_at,
             executed_at, customer_name, customer_phone, customer_address, distance_km, return_window_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (order_number, user["franchisee_id"], outlet_id, delivery_type, fulfillment_mode, final_status,
             item_subtotal, commission_total, delivery_charge, total, created_timestamp, executed_timestamp, customer_name,
             customer_phone, franchise_delivery_address, max(0, distance_km), return_window_days),
        )
        order_id = cur.lastrowid
        saved_profile = cur.execute('SELECT address FROM franchise_customer_profiles WHERE franchisee_id=? AND phone=?', (user['franchisee_id'],customer_phone)).fetchone()
        effective_address = buyer_address or (saved_profile['address'] if saved_profile else '')
        cur.execute('UPDATE orders SET buyer_address=? WHERE id=?',(effective_address,order_id))
        cur.execute("""INSERT INTO franchise_customer_profiles(franchisee_id,phone,name,notes,address,updated_at) VALUES(?,?,?,'',?,?)
            ON CONFLICT(franchisee_id,phone) DO UPDATE SET name=excluded.name,address=excluded.address,updated_at=excluded.updated_at""",(user['franchisee_id'],customer_phone,customer_name,effective_address,now_str()))
        cur.execute("UPDATE orders SET request_key=?, supply_state_code=?, supplier_state_code=?, wallet_required=?,delivery_cost=?,delivery_charge_bearer=? WHERE id=?", (request_key or uuid4().hex, franchise["state_code"], get_setting(cur, "business_state_code", "23"), wallet_required, delivery_cost, bearer, order_id))
        cur.execute(
            "UPDATE orders SET wallet_debit_amount=?, payable_to_outlet=?, updated_at=? WHERE id=?",
            (wallet_required if final_status == "EXECUTED" else 0, payable_to_outlet, now_str(), order_id),
        )
        for product, item_qty, selected_weight_kg, sale_price, line_total, unit_commission, line_commission, gst_rate, taxable_value, gst_amount in lines:
            line_weight = selected_weight_kg * item_qty
            cur.execute(
                """INSERT INTO order_items
                   (order_id, product_id, qty, unit_rsp, line_total, line_weight_kg, selected_weight_kg,
                    product_name_snapshot, hsn_code, gst_rate, taxable_value, gst_amount, slab_eligible, pack_label,
                    commission_per_unit, line_commission)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (order_id, product["id"], item_qty, sale_price, line_total, line_weight, selected_weight_kg,
                 product["product_name"], product["hsn_code"], gst_rate, taxable_value, gst_amount, product["slab_eligible"],
                 product["pack_label"] if not product["variable_weight"] else ("500 g" if selected_weight_kg == 0.5 else f"{selected_weight_kg:g} kg"),
                 unit_commission, line_commission),
            )

        if final_status == "EXECUTED":
            operations.reserve_order_inventory(cur, order_id, outlet_id, user["id"])

        if final_status == "EXECUTED":
            wallet_entry(cur, franchise["id"], "ORDER_DEBIT", -wallet_required, "ORDER", order_id, order_number, user["id"])

        add_order_event(cur, order_id, final_status, "Order created", user["id"])
        audit(cur, user, "CREATE", "ORDER", order_id, f"{order_number} {final_status} total {total:.2f}")

        if final_status == "EXECUTED":
            refresh_target_progress(cur, franchise["id"], user["id"])

        conn.commit()
    return RedirectResponse(
        f"/franchise/home?created={order_number}&status={final_status}#orders", status_code=303
    )
