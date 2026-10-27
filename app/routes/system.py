"""System HTTP routes."""
from fastapi import APIRouter

# Shared application services stay in app.main during the route extraction.
from app.core import *  # noqa: F401,F403

router = APIRouter()


@router.get('/api/ui-state')
def ui_state(request: Request):
    operations.require_user(request)
    from app.realtime import revision
    with get_conn() as conn:
        return {'revision': revision(conn)}


@router.get("/media/r2/{object_key:path}", include_in_schema=False)
def r2_media(request: Request, object_key: str) -> Response:
    if not current_user(request):
        return Response(status_code=403)
    config, _ = r2_config()
    allowed_prefix = f"{config['object_prefix']}/products/" if config else ""
    if not config or not object_key.startswith(allowed_prefix):
        return Response(status_code=404)
    try:
        stored = r2_client(config).get_object(Bucket=config["bucket_name"], Key=object_key)
        body = stored["Body"].read(MAX_PRODUCT_IMAGE_BYTES + 1)
        if len(body) > MAX_PRODUCT_IMAGE_BYTES:
            return Response(status_code=413)
        headers = {"Cache-Control": stored.get("CacheControl") or "private, max-age=86400"}
        if stored.get("ETag"):
            headers["ETag"] = stored["ETag"]
        return Response(content=body, media_type=stored.get("ContentType") or "application/octet-stream", headers=headers)
    except Exception:
        return Response(status_code=404)


@router.get("/api/summary", response_model=None)
def summary_api(request: Request):
    user = operations.require_user(request, {"ADMIN"})
    with get_conn() as conn:
        stats = {
            "orders_executed": conn.execute("SELECT COUNT(1) c FROM orders WHERE status='EXECUTED'").fetchone()["c"],
            "orders_on_hold": conn.execute("SELECT COUNT(1) c FROM orders WHERE status='ON_HOLD'").fetchone()["c"],
            "weight_alerts": conn.execute("SELECT COUNT(1) c FROM bills WHERE weight_check_status='ALERT'").fetchone()["c"]
            + conn.execute("SELECT COUNT(1) c FROM dockets WHERE weight_check_status='ALERT'").fetchone()["c"],
            "delivery_tasks": conn.execute("SELECT COUNT(1) c FROM delivery_tasks").fetchone()["c"],
        }
    return {"user": user["name"], "role": user["role"], "stats": stats}


@router.get("/ready")
def readiness():
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM settings LIMIT 1").fetchone()
        return {"status": "ready"}
    except sqlite3.Error:
        raise HTTPException(503, "Database unavailable")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": now_str()}
