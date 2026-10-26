"""Delivery HTTP routes."""
from fastapi import APIRouter

# Shared application services stay in app.main during the route extraction.
from app.core import *  # noqa: F401,F403

router = APIRouter()


@router.get("/delivery/home", response_class=HTMLResponse)
def delivery_home(request: Request):
    user = require_role(request, "DELIVERY")
    if isinstance(user, RedirectResponse):
        return user
    with get_conn() as conn:
        tasks = conn.execute(
            """
            SELECT t.*, f.name franchisee_name, f.shop_name, f.mobile, f.address franchise_address,
                   COALESCE(b.bill_number, d.docket_number) entity_number,
                   f.name customer_name,
                   f.mobile customer_phone,
                   COALESCE(NULLIF(f.delivery_address,''), f.address) delivery_address,
                   o.customer_name buyer_name,
                   COALESCE(o.total, 0) order_total
            FROM delivery_tasks t
            JOIN franchisees f ON f.id=t.franchisee_id
            LEFT JOIN bills b ON t.entity_type='BILL' AND b.id=t.entity_id
            LEFT JOIN orders o ON o.id=b.order_id
            LEFT JOIN dockets d ON t.entity_type='DOCKET' AND d.id=t.entity_id
            WHERE (t.assigned_user_id IS NULL OR t.assigned_user_id=?) AND f.outlet_id=?
            ORDER BY CASE WHEN t.status='DELIVERED' THEN 1 ELSE 0 END, t.id DESC
            """, (user["id"], user["outlet_id"])
        ).fetchall()
        task_ids = [task["id"] for task in tasks]
        proofs_by_task: dict[int, list[dict[str, Any]]] = {task_id: [] for task_id in task_ids}
        if task_ids:
            proofs = conn.execute(
                f"SELECT id,task_id,kind,created_at FROM proofs WHERE task_id IN ({','.join('?' for _ in task_ids)}) ORDER BY id DESC",
                task_ids,
            ).fetchall()
            for proof in proofs:
                proofs_by_task[proof["task_id"]].append(dict(proof))
        tasks = [dict(task) | {"proofs": proofs_by_task[task["id"]]} for task in tasks]
    return templates.TemplateResponse("delivery_home.html", {"request": request, "user": user, "tasks": tasks, "error": request.query_params.get("error", ""), "message": request.query_params.get("message", "")})


@router.post("/delivery/tasks")
def update_delivery_task(
    request: Request,
    task_id: int = Form(...),
    status: str = Form(...),
    pod_type: str = Form("OTP"),
    pod_reference: str = Form(""),
    remarks: str = Form(""),
) -> RedirectResponse:
    user = require_role(request, "DELIVERY")
    if isinstance(user, RedirectResponse):
        return user
    with get_conn() as conn:
        cur = conn.cursor()
        task = cur.execute("SELECT t.* FROM delivery_tasks t JOIN franchisees f ON f.id=t.franchisee_id WHERE t.id=? AND (t.assigned_user_id IS NULL OR t.assigned_user_id=?) AND f.outlet_id=?", (task_id, user["id"], user["outlet_id"])).fetchone()
        if not task:
            return RedirectResponse("/delivery/home", status_code=303)
        transitions = {"ASSIGNED": {"ACCEPTED"}, "ACCEPTED": {"PICKED_UP"}, "PICKED_UP": {"OUT_FOR_DELIVERY"}, "OUT_FOR_DELIVERY": {"DELIVERED"}}
        if status not in transitions.get(task["status"], set()):
            return RedirectResponse("/delivery/home?error=Invalid+task+transition", status_code=303)
        if status == "DELIVERED":
            operations.verify_completion(cur, task, pod_type, pod_reference, user)
        timestamp_column = {"ACCEPTED": "accepted_at", "PICKED_UP": "picked_at", "DELIVERED": "delivered_at"}.get(status)
        if timestamp_column:
            cur.execute(f"UPDATE delivery_tasks SET status=?, assigned_user_id=?, {timestamp_column}=?, pod_type=?, pod_reference=?, remarks=? WHERE id=?",
                        (status, user["id"], now_str(), pod_type, "VERIFIED_RECEIVER" if status == "DELIVERED" else pod_reference.strip(), remarks.strip(), task_id))
        else:
            cur.execute("UPDATE delivery_tasks SET status=?, assigned_user_id=?, remarks=? WHERE id=?", (status, user["id"], remarks.strip(), task_id))
        if status in {"PICKED_UP", "OUT_FOR_DELIVERY", "DELIVERED"}:
            table = "bills" if task["entity_type"] == "BILL" else "dockets"
            entity_status = "PICKED_BY_DELIVERY" if status == "PICKED_UP" else status
            cur.execute(f"UPDATE {table} SET status=?, updated_at=? WHERE id=?", (entity_status, now_str(), task["entity_id"]))
            if task["entity_type"] == "DOCKET":
                cur.execute("UPDATE bills SET status=?, updated_at=? WHERE id IN (SELECT bill_id FROM docket_bills WHERE docket_id=?)", (entity_status, now_str(), task["entity_id"]))
        for order_id in order_ids_for_entity(cur, task["entity_type"], task["entity_id"]):
            add_order_event(cur, order_id, status, remarks or f"Delivery task {status.lower()}", user["id"])
        if status == "DELIVERED":
            complete_entity_orders(cur, task["entity_type"], task["entity_id"], user["id"])
            cur.execute(
                "INSERT INTO handover_logs (entity_type, entity_id, from_role, to_role, notes, created_at, from_user_id, handover_mode) VALUES (?, ?, 'DELIVERY', 'FRANCHISEE', ?, ?, ?, ?)",
                (task["entity_type"], task["entity_id"], f"Delivered; {pod_type}; receiver verified", now_str(), user["id"], pod_type),
            )
        audit(cur, user, "TASK_STATUS", "DELIVERY_TASK", task_id, status)
        conn.commit()
    return RedirectResponse("/delivery/home", status_code=303)
