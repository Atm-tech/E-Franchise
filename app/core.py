"""Application setup and shared business services.

Workflow endpoints live in app.routes; app.main composes them for ASGI.
"""
import csv
from contextlib import closing
import io
import json
import math
import secrets
import time
import os
import re
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus
from uuid import uuid4
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from fastapi import HTTPException
from app.security import RequestProtection, hash_password, verify_password, session_secret
from app.database import PostgresConnection
from app import operations, dispatch_rules
from app.projections import monthly_projection
from app.order_journey import build_order_journey


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("E_FRANCHISE_DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = Path(os.getenv("E_FRANCHISE_DB", str(DATA_DIR / "e_franchise.db")))
MAX_PRODUCT_IMAGE_BYTES = 5 * 1024 * 1024
PRODUCT_IMAGE_SIGNATURES = {
    "image/jpeg": (".jpg", lambda data: data.startswith(b"\xff\xd8\xff")),
    "image/png": (".png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/webp": (".webp", lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"),
}

DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="E-Franchise MVP")
app.add_middleware(RequestProtection)
app.add_middleware(SessionMiddleware, secret_key=session_secret(DATA_DIR), https_only=os.getenv("APP_ENV") == "production", same_site="lax", max_age=28800)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
templates.env.globals["now_epoch"] = time.time
IST = ZoneInfo("Asia/Kolkata")


@app.get("/service-worker.js", include_in_schema=False)
def service_worker() -> FileResponse:
    return FileResponse(
        BASE_DIR / "app" / "static" / "service-worker.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


def now_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def monthly_period(reference: datetime | None = None) -> tuple[str, str, str]:
    current = reference.astimezone(IST) if reference else datetime.now(IST)
    start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"), start.strftime("%B %Y")


def get_conn() -> sqlite3.Connection:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return PostgresConnection(database_url)
    class ClosingConnection(sqlite3.Connection):
        def __exit__(self, *args):
            try:
                return super().__exit__(*args)
            finally:
                self.close()
    conn = sqlite3.connect(DB_PATH, timeout=15, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("BEGIN IMMEDIATE")
    return conn


def upsert_setting(cur: sqlite3.Cursor, key: str, value: str) -> None:
    cur.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def get_setting(cur: sqlite3.Cursor, key: str, default: str) -> str:
    row = cur.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def audit(
    cur: sqlite3.Cursor,
    user: dict[str, Any] | sqlite3.Row | None,
    action: str,
    entity_type: str,
    entity_id: int | None,
    details: str = "",
) -> None:
    cur.execute(
        "INSERT INTO audit_logs (actor_id, actor_role, action, entity_type, entity_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user["id"] if user else None, user["role"] if user else "SYSTEM", action, entity_type, entity_id, details, now_str()),
    )


def wallet_entry(
    cur: sqlite3.Cursor,
    franchisee_id: int,
    entry_type: str,
    amount: float,
    reference_type: str,
    reference_id: int | None,
    remarks: str,
    created_by: int | None,
) -> float:
    franchise = cur.execute("SELECT wallet_balance FROM franchisees WHERE id=?", (franchisee_id,)).fetchone()
    if not franchise:
        raise ValueError("Franchisee not found")
    if not math.isfinite(amount):
        raise ValueError("Amount must be finite")
    balance = round(float(franchise["wallet_balance"]) + float(amount), 2)
    if balance < 0:
        raise ValueError("Wallet balance cannot become negative")
    cur.execute("UPDATE franchisees SET wallet_balance=? WHERE id=?", (balance, franchisee_id))
    cur.execute(
        """
        INSERT INTO wallet_ledger
        (franchisee_id, entry_type, amount, balance_after, reference_type, reference_id, remarks, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (franchisee_id, entry_type, amount, balance, reference_type, reference_id, remarks, created_by, now_str()),
    )
    return balance


def earnings_wallet_entry(
    cur: sqlite3.Cursor,
    franchisee_id: int,
    entry_type: str,
    amount: float,
    reference_type: str,
    reference_id: int | None,
    remarks: str,
    created_by: int | None,
) -> float:
    cur.execute(
        "INSERT OR IGNORE INTO earnings_wallets (franchisee_id, balance, updated_at) VALUES (?, 0, ?)",
        (franchisee_id, now_str()),
    )
    wallet = cur.execute("SELECT balance FROM earnings_wallets WHERE franchisee_id=?", (franchisee_id,)).fetchone()
    if not math.isfinite(amount):
        raise ValueError("Amount must be finite")
    balance = round(float(wallet["balance"]) + float(amount), 2)
    if balance < 0:
        raise ValueError("Earnings wallet balance cannot become negative")
    cur.execute("UPDATE earnings_wallets SET balance=?, updated_at=? WHERE franchisee_id=?", (balance, now_str(), franchisee_id))
    cur.execute(
        """
        INSERT INTO earnings_wallet_ledger
        (franchisee_id, entry_type, amount, balance_after, reference_type, reference_id, remarks, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (franchisee_id, entry_type, amount, balance, reference_type, reference_id, remarks, created_by, now_str()),
    )
    return balance


def immediate_profit_credit_enabled(cur):
    return os.getenv('APP_ENV', '').lower() != 'production' and not os.getenv('DATABASE_URL', '').strip() and get_setting(cur, 'profit_credit_mode', 'delayed') == 'immediate'


def release_matured_commissions(cur: sqlite3.Cursor, franchisee_id: int | None = None) -> int:
    if os.getenv("DATABASE_URL", "").strip():
        cur.execute("SELECT pg_advisory_xact_lock(72419041)")
    """Move commissions past their order-specific return window into spendable earnings."""
    params: list[Any] = [now_str(), int(immediate_profit_credit_enabled(cur))]
    filter_sql = ""
    if franchisee_id is not None:
        filter_sql = " AND o.franchisee_id=?"
        params.append(franchisee_id)
    orders = cur.execute(
        """SELECT o.id, o.franchisee_id, o.commission_total FROM orders o
           WHERE o.status='DELIVERED' AND o.commission_released_at IS NULL
             AND o.commission_available_at IS NOT NULL AND (o.commission_available_at<=? OR ?=1)""" + filter_sql,
        params,
    ).fetchall()
    for order in orders:
        reversed_row = cur.execute(
            "SELECT COALESCE(SUM(commission_reversed),0) amount FROM order_returns WHERE order_id=?",
            (order["id"],),
        ).fetchone()
        amount = round(max(0, float(order["commission_total"] or 0) - float(reversed_row["amount"] or 0)), 2)
        if amount > 0:
            earnings_wallet_entry(
                cur, order["franchisee_id"], "ORDER_COMMISSION", amount, "ORDER_COMMISSION", order["id"],
                "Profit credited immediately in local test mode" if immediate_profit_credit_enabled(cur) else "Profit credited after customer return window", None,
            )
        cur.execute("UPDATE orders SET commission_released_at=? WHERE id=? AND commission_released_at IS NULL", (now_str(), order["id"]))
    return len(orders)


def add_order_event(cur: sqlite3.Cursor, order_id: int, status: str, notes: str, user_id: int | None) -> None:
    cur.execute(
        "INSERT INTO order_events (order_id, status, notes, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
        (order_id, status, notes, user_id, now_str()),
    )


def resolve_commission(cur: sqlite3.Cursor, product: sqlite3.Row, line_total: float) -> float:
    rule = cur.execute(
        "SELECT * FROM commission_rules WHERE status='ACTIVE' AND product_id=? AND (starts_on IS NULL OR starts_on='' OR starts_on<=?) AND (ends_on IS NULL OR ends_on='' OR ends_on>=?) ORDER BY id DESC LIMIT 1",
        (product["id"], now_str()[:10], now_str()[:10]),
    ).fetchone()
    if not rule:
        rule = cur.execute(
            "SELECT * FROM commission_rules WHERE status='ACTIVE' AND scope='DEPARTMENT' AND lower(department)=lower(?) AND (starts_on IS NULL OR starts_on='' OR starts_on<=?) AND (ends_on IS NULL OR ends_on='' OR ends_on>=?) ORDER BY id DESC LIMIT 1",
            (product["department"], now_str()[:10], now_str()[:10]),
        ).fetchone()
    if rule:
        value = float(rule["commission_value"])
        return min(line_total, value if rule["commission_type"] == "FIXED" else line_total * value / 100.0)
    return line_total * float(product["commission_value"]) / 100.0


def resolve_deal(cur: sqlite3.Cursor, product: sqlite3.Row, unit_price: float, franchisee_id: int | None = None) -> tuple[float, str]:
    today = now_str()[:10]
    date_filter = "(franchisee_id IS NULL OR franchisee_id=?) AND status='ACTIVE' AND (starts_on IS NULL OR starts_on='' OR starts_on<=?) AND (ends_on IS NULL OR ends_on='' OR ends_on>=?)"
    deal = cur.execute(
        f"SELECT * FROM promotions WHERE {date_filter} AND scope='PRODUCT' AND product_id=? ORDER BY (franchisee_id IS NOT NULL) DESC,id DESC LIMIT 1",
        (franchisee_id, today, today, product["id"]),
    ).fetchone()
    if not deal:
        deal = cur.execute(
            f"SELECT * FROM promotions WHERE {date_filter} AND scope='DEPARTMENT' AND lower(department)=lower(?) ORDER BY (franchisee_id IS NOT NULL) DESC,id DESC LIMIT 1",
            (franchisee_id, today, today, product["department"]),
        ).fetchone()
    if not deal:
        return unit_price, ""
    value = float(deal["discount_value"])
    discount = value if deal["discount_type"] == "FIXED" else unit_price * value / 100.0
    return round(max(0, unit_price - discount), 2), deal["name"]


def target_sales_amount(
    cur: sqlite3.Cursor,
    program: sqlite3.Row,
    franchisee_id: int,
    period_start: str,
    period_end: str,
    *, include_confirmed: bool = False,
) -> float:
    sales_time = "COALESCE(NULLIF(o.executed_at,''), o.created_at)" if include_confirmed else "COALESCE(NULLIF(o.delivered_at,''), NULLIF(o.updated_at,''), o.created_at)"
    status_filter = "o.status NOT IN ('ON_HOLD','CANCELLED')" if include_confirmed else "o.status='DELIVERED'"
    filters = f"o.franchisee_id=? AND {sales_time}>=? AND {sales_time}<? AND {status_filter}"
    net_line = "CASE WHEN oi.qty>COALESCE(ret.returned_qty,0) THEN oi.line_total*(oi.qty-COALESCE(ret.returned_qty,0))*1.0/oi.qty ELSE 0 END"
    params: list[Any] = [franchisee_id, period_start, period_end]
    if program["starts_on"]:
        filters += f" AND substr({sales_time},1,10)>=?"
        params.append(program["starts_on"])
    if program["ends_on"]:
        filters += f" AND substr({sales_time},1,10)<=?"
        params.append(program["ends_on"])
    if program["scope"] == "TOTAL_SALE":
        row = cur.execute(
            f"""SELECT COALESCE(SUM({net_line}),0) total
                FROM order_items oi JOIN orders o ON o.id=oi.order_id JOIN products p ON p.id=oi.product_id
                LEFT JOIN (SELECT order_item_id,SUM(quantity) returned_qty FROM order_returns GROUP BY order_item_id) ret ON ret.order_item_id=oi.id
                WHERE {filters} AND oi.slab_eligible=1""", params,
        ).fetchone()
    elif program["scope"] == "DEPARTMENT":
        row = cur.execute(
            f"""SELECT COALESCE(SUM({net_line}),0) total
                FROM order_items oi JOIN orders o ON o.id=oi.order_id JOIN products p ON p.id=oi.product_id
                LEFT JOIN (SELECT order_item_id,SUM(quantity) returned_qty FROM order_returns GROUP BY order_item_id) ret ON ret.order_item_id=oi.id
                WHERE {filters} AND oi.slab_eligible=1 AND lower(p.department)=lower(?)""",
            params + [program["department"] or ""],
        ).fetchone()
    else:
        row = cur.execute(
            f"""SELECT COALESCE(SUM({net_line}),0) total
                FROM order_items oi JOIN orders o ON o.id=oi.order_id JOIN products p ON p.id=oi.product_id
                LEFT JOIN (SELECT order_item_id,SUM(quantity) returned_qty FROM order_returns GROUP BY order_item_id) ret ON ret.order_item_id=oi.id
                WHERE {filters} AND oi.slab_eligible=1 AND oi.product_id=?""",
            params + [program["product_id"]],
        ).fetchone()
    return round(float(row["total"] or 0), 2)


def refresh_target_progress(cur: sqlite3.Cursor, franchisee_id: int, created_by: int | None = None) -> None:
    period_start, period_end, _ = monthly_period()
    programs = cur.execute(
        """SELECT * FROM target_programs
           WHERE status='ACTIVE' AND period_type='MONTHLY'
             AND (starts_on IS NULL OR starts_on='' OR starts_on<=?)
             AND (ends_on IS NULL OR ends_on='' OR ends_on>=?)
           ORDER BY id""",
        (period_end[:10], period_start[:10]),
    ).fetchall()
    for program in programs:
        sales = target_sales_amount(cur, program, franchisee_id, period_start, period_end)
        slabs = cur.execute(
            "SELECT * FROM target_slabs WHERE program_id=? AND status='ACTIVE' ORDER BY threshold_amount",
            (program["id"],),
        ).fetchall()
        achieved = next((slab for slab in reversed(slabs) if sales >= float(slab["threshold_amount"])), None)
        if achieved:
            reward_basis = sales if program["reward_type"] == "PERCENT_OF_SALES" else float(achieved["threshold_amount"])
            reward = round(reward_basis * float(achieved["reward_percent"]) / 100, 2)
        else:
            reward = 0.0
        existing = cur.execute(
            "SELECT * FROM franchise_target_progress WHERE franchisee_id=? AND program_id=? AND period_start=?",
            (franchisee_id, program["id"], period_start),
        ).fetchone()
        credited = float(existing["credited_reward"] or 0) if existing else 0.0
        cur.execute(
            """INSERT INTO franchise_target_progress
               (franchisee_id, program_id, period_start, period_end, sales_amount, achieved_slab_id, earned_reward, credited_reward, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
               ON CONFLICT(franchisee_id, program_id, period_start) DO UPDATE SET
                 period_end=excluded.period_end, sales_amount=excluded.sales_amount,
                 achieved_slab_id=excluded.achieved_slab_id, earned_reward=excluded.earned_reward,
                 updated_at=excluded.updated_at""",
            (franchisee_id, program["id"], period_start, period_end, sales, achieved["id"] if achieved else None,
             reward, credited, now_str()),
        )
        progress = cur.execute(
            "SELECT id, credited_reward FROM franchise_target_progress WHERE franchisee_id=? AND program_id=? AND period_start=?",
            (franchisee_id, program["id"], period_start),
        ).fetchone()
        delta = round(max(0, reward - float(progress["credited_reward"] or 0)), 2)
        if delta > 0:
            balance = earnings_wallet_entry(
                cur, franchisee_id, "TARGET_REWARD", delta, "TARGET_PROGRESS", progress["id"],
                f"{program['name']} target reward", created_by,
            )
            cur.execute(
                "UPDATE franchise_target_progress SET credited_reward=?, credited_at=?, status='REWARDED', updated_at=? WHERE id=?",
                (reward, now_str(), now_str(), progress["id"]),
            )
            audit(cur, None, "TARGET_REWARD", "EARNINGS_WALLET", franchisee_id, f"{program['code']}: +{delta:.2f}; balance {balance:.2f}")


def target_dashboard(cur: sqlite3.Cursor, franchisee_id: int) -> list[dict[str, Any]]:
    period_start, _, period_label = monthly_period()
    rows = cur.execute(
        """SELECT tp.*, ftp.sales_amount, ftp.earned_reward, ftp.credited_reward, ftp.achieved_slab_id
           FROM target_programs tp
           LEFT JOIN franchise_target_progress ftp
             ON ftp.program_id=tp.id AND ftp.franchisee_id=? AND ftp.period_start=?
           WHERE tp.status='ACTIVE' ORDER BY tp.id""",
        (franchisee_id, period_start),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        slabs = [dict(slab) for slab in cur.execute(
            "SELECT * FROM target_slabs WHERE program_id=? AND status='ACTIVE' ORDER BY threshold_amount", (row["id"],)
        ).fetchall()]
        sales = target_sales_amount(cur, row, franchisee_id, *monthly_period()[:2], include_confirmed=True)
        earned = float(row["earned_reward"] or 0)
        achieved = next((slab for slab in reversed(slabs) if sales >= float(slab['threshold_amount'])), None)
        for slab in slabs:
            slab['reward_at_milestone'] = round(float(slab['threshold_amount']) * float(slab['reward_percent']) / 100, 2)
            slab['remaining_sales'] = round(max(0, float(slab['threshold_amount']) - sales), 2)
            slab['additional_reward'] = round(max(0, slab['reward_at_milestone'] - earned), 2)
        current_reward_rate = float(achieved['reward_percent']) if achieved else 0
        next_slab = next((slab for slab in slabs if sales < float(slab["threshold_amount"])), None)
        progress_target = float(next_slab["threshold_amount"]) if next_slab else (float(slabs[-1]["threshold_amount"]) if slabs else 1)
        result.append(dict(row) | {
            "sales_amount": sales,
            "current_reward_rate": current_reward_rate,
            "earned_reward": float(row["earned_reward"] or 0),
            "credited_reward": float(row["credited_reward"] or 0),
            "slabs": slabs,
            "next_slab": next_slab,
            "progress_percent": min(100, round(sales / progress_target * 100, 1)) if progress_target else 0,
            "period_label": period_label,
        })
    return result


def earning_guide(
    cur: sqlite3.Cursor,
    franchisee_id: int,
    products: list[dict[str, Any]],
    targets: list[dict[str, Any]],
) -> dict[str, Any]:
    period_start, period_end, period_label = monthly_period()
    stats = cur.execute(
        """SELECT COUNT(1) order_count,
                  COUNT(DISTINCT NULLIF(customer_phone,'')) customer_count,
                  COALESCE(SUM(item_subtotal),0) sales,
                  COALESCE(SUM(commission_total),0) commission
           FROM orders
           WHERE franchisee_id=?
             AND COALESCE(NULLIF(executed_at,''),created_at)>=?
             AND COALESCE(NULLIF(executed_at,''),created_at)<?
             AND status NOT IN ('ON_HOLD','CANCELLED')""",
        (franchisee_id, period_start, period_end),
    ).fetchone()
    order_count = int(stats["order_count"] or 0)
    customer_count = int(stats["customer_count"] or 0)
    sales = float(stats["sales"] or 0)
    commission = float(stats["commission"] or 0)
    eligible_sales = float(next((target["sales_amount"] for target in targets if target["scope"] == "TOTAL_SALE"), sales))
    average_basket = round(sales / order_count, 2) if order_count else 500.0
    product_rates = [
        float(product["commission_per_unit"]) / float(product["sale_price"]) * 100
        for product in products if float(product["sale_price"] or 0) > 0
    ]
    average_commission_rate = round(
        commission / sales * 100 if sales else (sum(product_rates) / len(product_rates) if product_rates else 0), 2
    )
    ranked_products = []
    for product in products:
        price = float(product["sale_price"] or 0)
        ranked_products.append(product | {
            "commission_rate": round(float(product["commission_per_unit"]) / price * 100, 2) if price else 0,
        })
    ranked_products.sort(key=lambda item: (float(item["commission_per_unit"]), item["commission_rate"]), reverse=True)

    next_target = next((target for target in targets if target.get("next_slab")), None)
    remaining_sales = 0.0
    orders_needed = 0
    if next_target:
        remaining_sales = max(0, float(next_target["next_slab"]["threshold_amount"]) - float(next_target["sales_amount"]))
        orders_needed = math.ceil(remaining_sales / average_basket) if average_basket else 0
    current = datetime.now(IST)
    end = datetime.strptime(period_end, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    days_left = max(1, (end.date() - current.date()).days)
    daily_customers_needed = math.ceil(orders_needed / days_left) if orders_needed else 0
    slabs = [
        {"threshold": float(slab["threshold_amount"]), "rate": float(slab["reward_percent"])}
        for target in targets for slab in target.get("slabs", []) if target["scope"] == "TOTAL_SALE"
    ]
    delivered = cur.execute(
        """SELECT COALESCE(SUM(item_subtotal),0) sales,
                  COALESCE(SUM(commission_total),0) commission
           FROM orders WHERE franchisee_id=? AND status='DELIVERED'
             AND COALESCE(NULLIF(updated_at,''),created_at)>=?
             AND COALESCE(NULLIF(updated_at,''),created_at)<?""",
        (franchisee_id, period_start, period_end),
    ).fetchone()
    projection_targets = [target for target in targets
                          if target['period_type'] == 'MONTHLY'
                          and (not target['starts_on'] or target['starts_on'] <= current.date().isoformat())
                          and (not target['ends_on'] or target['ends_on'] >= current.date().isoformat())]
    projection = monthly_projection(float(delivered['sales']), float(delivered['commission']), projection_targets, current)
    return {
        "projection": projection,
        "period_label": period_label,
        "sales": eligible_sales,
        "gross_sales": sales,
        "eligible_sales": eligible_sales,
        "commission": commission,
        "order_count": order_count,
        "customer_count": customer_count,
        "average_basket": average_basket,
        "average_commission_rate": average_commission_rate,
        "top_products": ranked_products[:3],
        "next_target": next_target,
        "remaining_sales": remaining_sales,
        "orders_needed": orders_needed,
        "days_left": days_left,
        "daily_customers_needed": daily_customers_needed,
        "slabs": slabs,
    }


def report_period(period: str, date_from: str = "", date_to: str = "") -> tuple[str, str, str]:
    current = datetime.now(IST)
    period = period if period in {"day", "month", "year", "custom"} else "month"
    if period == "day":
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        label = f"Today · {start.strftime('%d %b %Y')}"
    elif period == "year":
        start = current.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1)
        label = f"Year to date · {start.year}"
    elif period == "custom" and date_from and date_to:
        try:
            start = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=IST)
            last = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=IST)
        except ValueError:
            return report_period("month")
        if last < start:
            start, last = last, start
        end = last + timedelta(days=1)
        label = f"{start.strftime('%d %b %Y')} – {last.strftime('%d %b %Y')}"
    else:
        start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
        label = f"Month to date · {start.strftime('%B %Y')}"
        period = "month"
    return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"), label


def franchise_summary(cur: sqlite3.Cursor, franchisee_id: int, start: str, end: str, label: str) -> dict[str, Any]:
    sales_time = "COALESCE(NULLIF(executed_at,''),created_at)"
    metrics = cur.execute(
            f"""SELECT COUNT(1) orders, COUNT(DISTINCT NULLIF(customer_phone,'')) customers,
                   COALESCE(SUM(item_subtotal),0) sales,
                   COALESCE(SUM(CASE WHEN status='DELIVERED' THEN commission_total ELSE 0 END),0) commission,
                   COALESCE(SUM(delivery_charge),0) delivery
            FROM orders WHERE franchisee_id=? AND {sales_time}>=? AND {sales_time}<?
              AND status NOT IN ('ON_HOLD','CANCELLED')""", (franchisee_id, start, end),
    ).fetchone()
    eligible = cur.execute(
        f"""SELECT COALESCE(SUM(oi.line_total),0) total FROM order_items oi
            JOIN orders o ON o.id=oi.order_id
            WHERE o.franchisee_id=? AND COALESCE(NULLIF(o.executed_at,''),o.created_at)>=?
              AND COALESCE(NULLIF(o.executed_at,''),o.created_at)<?
              AND o.status NOT IN ('ON_HOLD','CANCELLED') AND oi.slab_eligible=1""", (franchisee_id, start, end),
    ).fetchone()["total"]
    wallet = cur.execute(
        """SELECT COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0) money_in,
                  COALESCE(SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END),0) money_out
           FROM wallet_ledger WHERE franchisee_id=? AND created_at>=? AND created_at<?""", (franchisee_id, start, end),
    ).fetchone()
    target_rewards = cur.execute(
        """SELECT COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0) rewards,
                  COALESCE(SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END),0) transferred
           FROM earnings_wallet_ledger WHERE franchisee_id=? AND entry_type='TARGET_REWARD' AND created_at>=? AND created_at<?""", (franchisee_id, start, end),
    ).fetchone()
    tax_rows = cur.execute(
        """SELECT oi.gst_rate, ROUND(SUM(oi.taxable_value),2) taxable, ROUND(SUM(oi.gst_amount),2) tax,
                  ROUND(SUM(oi.line_total),2) gross,
                  ROUND(SUM(CASE WHEN o.supply_state_code=o.supplier_state_code THEN ROUND(oi.gst_amount/2,2) ELSE 0 END),2) cgst,
                  ROUND(SUM(CASE WHEN o.supply_state_code=o.supplier_state_code THEN oi.gst_amount-ROUND(oi.gst_amount/2,2) ELSE 0 END),2) sgst,
                  ROUND(SUM(CASE WHEN o.supply_state_code!=o.supplier_state_code THEN oi.gst_amount ELSE 0 END),2) igst
           FROM order_items oi JOIN orders o ON o.id=oi.order_id
           WHERE o.franchisee_id=? AND COALESCE(NULLIF(o.executed_at,''),o.created_at)>=?
             AND COALESCE(NULLIF(o.executed_at,''),o.created_at)<? AND o.status NOT IN ('ON_HOLD','CANCELLED')
           GROUP BY oi.gst_rate ORDER BY oi.gst_rate""", (franchisee_id, start, end),
    ).fetchall()
    orders = int(metrics["orders"] or 0)
    return {
        "label": label, "start": start, "end": end,
        "orders": orders, "customers": int(metrics["customers"] or 0),
        "sales": round(float(metrics["sales"] or 0), 2),
        "eligible_sales": round(float(eligible or 0), 2),
        "commission": round(float(metrics["commission"] or 0), 2),
        "delivery": round(float(metrics["delivery"] or 0), 2),
        "average_basket": round(float(metrics["sales"] or 0) / orders, 2) if orders else 0,
        "wallet_in": round(float(wallet["money_in"] or 0), 2),
        "wallet_out": round(float(wallet["money_out"] or 0), 2),
        "target_rewards": round(float(target_rewards["rewards"] or 0), 2),
        "earnings_transferred": round(float(target_rewards["transferred"] or 0), 2),
        "tax_breakdown": [dict(row) for row in tax_rows],
        "total_tax": round(sum(float(row["tax"] or 0) for row in tax_rows), 2),
    }


def invoice_tax_breakdown(items: list[dict[str, Any]], delivery_charge: float = 0, interstate: bool = False) -> list[dict[str, float]]:
    grouped: dict[float, dict[str, float]] = {}
    for item in items:
        rate = float(item["gst_rate"] or 0)
        bucket = grouped.setdefault(rate, {"rate": rate, "taxable": 0, "gst": 0, "gross": 0})
        bucket["taxable"] += float(item["taxable_value"] or 0)
        bucket["gst"] += float(item["gst_amount"] or 0)
        bucket["gross"] += float(item["line_total"] or 0)
    if delivery_charge > 0:
        rate = 18.0
        taxable = round(delivery_charge * 100 / 118, 2)
        bucket = grouped.setdefault(rate, {"rate": rate, "taxable": 0, "gst": 0, "gross": 0})
        bucket["taxable"] += taxable
        bucket["gst"] += delivery_charge - taxable
        bucket["gross"] += delivery_charge
    result = []
    for rate in sorted(grouped):
        bucket = grouped[rate]
        result.append({key: round(value, 2) for key, value in bucket.items()} | {
            "cgst": 0 if interstate else round(bucket["gst"] / 2, 2), "sgst": 0 if interstate else round(bucket["gst"] - round(bucket["gst"] / 2, 2), 2), "igst": round(bucket["gst"], 2) if interstate else 0,
        })
    return result


def order_ids_for_entity(cur: sqlite3.Cursor, entity_type: str, entity_id: int) -> list[int]:
    if entity_type == "BILL":
        row = cur.execute("SELECT order_id FROM bills WHERE id=?", (entity_id,)).fetchone()
        return [row["order_id"]] if row else []
    rows = cur.execute(
        "SELECT b.order_id FROM docket_bills db JOIN bills b ON b.id=db.bill_id WHERE db.docket_id=?",
        (entity_id,),
    ).fetchall()
    return [row["order_id"] for row in rows]


def complete_entity_orders(cur: sqlite3.Cursor, entity_type: str, entity_id: int, user_id: int | None) -> None:
    table = "bills" if entity_type == "BILL" else "dockets"
    cur.execute(f"UPDATE {table} SET status='DELIVERED', updated_at=? WHERE id=?", (now_str(), entity_id))
    if entity_type == "DOCKET":
        cur.execute(
            "UPDATE bills SET status='DELIVERED', updated_at=? WHERE id IN (SELECT bill_id FROM docket_bills WHERE docket_id=?)",
            (now_str(), entity_id),
        )
    for order_id in order_ids_for_entity(cur, entity_type, entity_id):
        cur.execute("UPDATE orders SET status='DELIVERED', updated_at=? WHERE id=?", (now_str(), order_id))
        add_order_event(cur, order_id, "DELIVERED", "Fulfilment completed", user_id)
        order = cur.execute("SELECT franchisee_id, return_window_days FROM orders WHERE id=?", (order_id,)).fetchone()
        fid = order["franchisee_id"]
        delivered_at = now_str()
        available_at = (datetime.strptime(delivered_at, "%Y-%m-%d %H:%M:%S") + timedelta(days=int(order["return_window_days"] or 4))).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "UPDATE orders SET delivered_at=?, commission_available_at=?, updated_at=? WHERE id=?",
            (delivered_at, available_at, delivered_at, order_id),
        )
        refresh_target_progress(cur, fid, user_id)
        if immediate_profit_credit_enabled(cur):
            release_matured_commissions(cur, fid)


def read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def shared_r2_values() -> dict[str, str]:
    configured_file = os.getenv("R2_CONFIG_FILE", "").strip()
    candidates = [Path(configured_file)] if configured_file else []
    candidates.extend([BASE_DIR / ".env", BASE_DIR.parent / "Sales managment" / ".env"])
    required = {"R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"}
    for candidate in candidates:
        values = read_env_file(candidate)
        if required.issubset(key for key, value in values.items() if value):
            return values
    return {}


def r2_config() -> tuple[dict[str, str] | None, list[str]]:
    shared = shared_r2_values()

    def value(name: str) -> str:
        return os.getenv(name, "").strip() or shared.get(name, "").strip()

    values = {
        "endpoint_url": value("R2_ENDPOINT_URL"),
        "access_key_id": value("R2_ACCESS_KEY_ID"),
        "secret_access_key": value("R2_SECRET_ACCESS_KEY"),
        "bucket_name": value("R2_BUCKET_NAME"),
        "public_base_url": os.getenv("R2_PUBLIC_BASE_URL", "").strip().rstrip("/"),
        "object_prefix": os.getenv("R2_OBJECT_PREFIX", "e-franchise").strip().strip("/") or "e-franchise",
    }
    account_id = value("R2_ACCOUNT_ID")
    if not values["endpoint_url"] and account_id:
        values["endpoint_url"] = f"https://{account_id}.r2.cloudflarestorage.com"
    required = ("endpoint_url", "access_key_id", "secret_access_key", "bucket_name")
    missing = [key for key in required if not values[key]]
    return (values if not missing else None), missing


def r2_client(config: dict[str, str]):
    try:
        import boto3
    except ImportError as exc:
        raise ValueError("R2 support is unavailable. Install the project requirements.") from exc
    return boto3.client(
        service_name="s3",
        endpoint_url=config["endpoint_url"],
        aws_access_key_id=config["access_key_id"],
        aws_secret_access_key=config["secret_access_key"],
        region_name="auto",
    )


def upload_product_image(upload: UploadFile, sku_code: str) -> str:
    config, missing = r2_config()
    if not config:
        raise ValueError(f"R2 is not configured. Missing: {', '.join(missing)}.")

    content_type = (upload.content_type or "").lower()
    signature = PRODUCT_IMAGE_SIGNATURES.get(content_type)
    if not signature:
        raise ValueError("Product image must be JPEG, PNG, or WebP.")

    content = upload.file.read(MAX_PRODUCT_IMAGE_BYTES + 1)
    if not content:
        raise ValueError("The selected product image is empty.")
    if len(content) > MAX_PRODUCT_IMAGE_BYTES:
        raise ValueError("Product image must be 5 MB or smaller.")

    extension, matches_signature = signature
    if not matches_signature(content):
        raise ValueError("The uploaded file content does not match its image type.")

    safe_sku = re.sub(r"[^a-zA-Z0-9_-]+", "-", sku_code).strip("-").lower() or "product"
    object_key = f"{config['object_prefix']}/products/{safe_sku}-{uuid4().hex}{extension}"
    try:
        client = r2_client(config)
        client.put_object(
            Bucket=config["bucket_name"],
            Key=object_key,
            Body=content,
            ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable",
        )
    except Exception as exc:
        raise ValueError("Cloudflare R2 rejected the image upload. Check the bucket credentials and endpoint.") from exc

    if config["public_base_url"]:
        return f"{config['public_base_url']}/{quote(object_key, safe='/')}"
    return f"/media/r2/{quote(object_key, safe='/')}"




from app.db_setup import init_db


@app.on_event("startup")
def on_startup() -> None:
    if os.getenv("DATABASE_URL", "").strip():
        init_db()
        return
    if DB_PATH.exists() and DB_PATH.stat().st_size:
        with closing(sqlite3.connect(DB_PATH)) as existing:
            version = existing.execute("PRAGMA user_version").fetchone()[0]
            if version < 3:
                folder = DATA_DIR / "backups"
                folder.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(folder / f"pre-upgrade-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.db")) as backup:
                    existing.backup(backup)
    init_db()

# Database initialization runs only inside the application lifecycle.


def credit_due_profits():
    with get_conn() as conn:
        return release_matured_commissions(conn.cursor())


@app.on_event("startup")
def start_profit_worker():
    from app.profit_worker import ProfitWorker
    app.state.profit_worker = None
    if os.getenv("APP_ENV") == "test" or os.getenv("PROFIT_WORKER_ENABLED", "1") == "0":
        return
    interval = max(1, float(os.getenv("PROFIT_CHECK_INTERVAL_SECONDS", "60")))
    worker = ProfitWorker(credit_due_profits, interval)
    app.state.profit_worker = worker
    worker.start()


@app.on_event("shutdown")
def stop_profit_worker():
    worker = getattr(app.state, "profit_worker", None)
    if worker:
        worker.stop()


def current_user(request: Request) -> dict[str, Any] | None:
    if getattr(request.state, 'bearer_user', None):
        return request.state.bearer_user
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with get_conn() as conn:
        testing_until = request.session.get("testing_quick_login_until")
        if testing_until is not None and (
            time.time() >= float(testing_until) or not testing_login_deadline(conn.cursor())
        ):
            request.session.clear()
            return None
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row or row["status"] != "ACTIVE" or request.session.get("auth_version") != row["auth_version"]:
            return None
        if row["role"] == "FRANCHISEE":
            franchise = conn.execute("SELECT status FROM franchisees WHERE id=?", (row["franchisee_id"],)).fetchone()
            if not franchise or franchise["status"] != "ACTIVE":
                return None
        return dict(row)


def require_role(request: Request, role: str) -> dict[str, Any] | RedirectResponse:
    user = current_user(request)
    if not user or user["role"] != role:
        return RedirectResponse("/login", status_code=303)
    return user




def next_code(prefix: str, table: str, column: str) -> str:
    return prefix + uuid4().hex[:12].upper()


def to_kg(unit_weight: float, unit: str) -> float:
    return unit_weight / 1000.0 if unit.upper() == "G" else unit_weight


def normalize_header(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def parse_decimal(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    return float(str(value).strip())


def parse_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    return int(float(str(value).strip()))


def parse_csv_products(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("The file is empty.")
    return [{normalize_header(k): (v or "").strip() for k, v in row.items() if k} for row in reader]


def get_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []

    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    root = ET.fromstring(raw)
    values: list[str] = []
    for si in root.findall("x:si", ns):
        text_parts = [node.text or "" for node in si.findall(".//x:t", ns)]
        values.append("".join(text_parts))
    return values


def parse_xlsx_products(content: bytes) -> list[dict[str, str]]:
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        shared_strings = get_shared_strings(archive)
        try:
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        except KeyError as exc:
            raise ValueError("Could not read the first worksheet.") from exc

    def column_index(cell_ref: str) -> int:
        letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
        index = 0
        for ch in letters:
            index = (index * 26) + (ord(ch) - ord("A") + 1)
        return max(index - 1, 0)

    rows: list[list[str]] = []
    for row in sheet.findall(".//x:sheetData/x:row", ns):
        values: list[str] = []
        for cell in row.findall("x:c", ns):
            cell_type = cell.attrib.get("t")
            cell_ref = cell.attrib.get("r", "")
            target_index = column_index(cell_ref) if cell_ref else len(values)
            while len(values) <= target_index:
                values.append("")
            value_node = cell.find("x:v", ns)
            text_node = cell.find("x:is/x:t", ns)
            value = ""
            if cell_type == "s" and value_node is not None:
                index = int(value_node.text or "0")
                value = shared_strings[index] if index < len(shared_strings) else ""
            elif text_node is not None:
                value = text_node.text or ""
            elif value_node is not None:
                value = value_node.text or ""
            values[target_index] = value.strip()
        if any(values):
            rows.append(values)

    if not rows:
        raise ValueError("The file is empty.")

    headers = [normalize_header(cell) for cell in rows[0]]
    return [
        {headers[index]: row[index].strip() if index < len(row) else "" for index in range(len(headers))}
        for row in rows[1:]
        if any((cell or "").strip() for cell in row)
    ]


def parse_product_upload(upload: UploadFile) -> list[dict[str, str]]:
    filename = upload.filename or ""
    suffix = Path(filename).suffix.lower()
    content = upload.file.read()
    if not content:
        raise ValueError("Please choose a file with product data.")
    if suffix == ".csv":
        return parse_csv_products(content)
    if suffix == ".xlsx":
        return parse_xlsx_products(content)
    raise ValueError("Unsupported file type. Use .xlsx or .csv.")


def product_values_from_row(row: dict[str, Any]) -> tuple[Any, ...]:
    sku_code = str(row.get("sku_code", "")).strip()
    product_name = str(row.get("product_name", "")).strip()
    department = str(row.get("department", "")).strip()
    if not sku_code or not product_name or not department:
        raise ValueError("sku_code, product_name, and department are required.")

    mrp = parse_decimal(row.get("mrp"))
    rsp = parse_decimal(row.get("rsp"))
    unit_weight = parse_decimal(row.get("unit_weight"), 0.0)
    weight_unit = str(row.get("weight_unit", "KG") or "KG").strip().upper()
    pack_multiplier = parse_int(row.get("pack_multiplier"), 1)
    commission_mode = str(row.get("commission_mode", "DEPARTMENT") or "DEPARTMENT").strip().upper()
    commission_value = parse_decimal(row.get("commission_value"), 0.0)
    status = str(row.get("status", "ACTIVE") or "ACTIVE").strip().upper()
    image_url = str(row.get("image_url", "") or "").strip()
    brand = str(row.get("brand", "") or "").strip()
    category = str(row.get("category", "") or "").strip()
    subcategory = str(row.get("subcategory", "") or "").strip()
    hsn_code = str(row.get("hsn_code", "") or "").strip()
    gst_rate = parse_decimal(row.get("gst_rate"), 0.0)
    price_includes_gst = 1 if str(row.get("price_includes_gst", "1")).lower() not in {"0", "false", "no"} else 0
    slab_eligible = 1 if str(row.get("slab_eligible", "1")).lower() not in {"0", "false", "no"} else 0
    attributes_json = str(row.get("attributes_json", "{}") or "{}").strip()
    try:
        json.loads(attributes_json)
    except json.JSONDecodeError as exc:
        raise ValueError("attributes_json must contain valid JSON.") from exc

    if weight_unit not in {"KG", "G"}:
        raise ValueError("weight_unit must be KG or G.")
    if commission_mode not in {"DEPARTMENT", "PRODUCT"}:
        raise ValueError("commission_mode must be DEPARTMENT or PRODUCT.")
    if status not in {"ACTIVE", "INACTIVE"}:
        raise ValueError("status must be ACTIVE or INACTIVE.")
    if pack_multiplier < 1:
        raise ValueError("pack_multiplier must be at least 1.")
    if not all(math.isfinite(v) for v in (mrp, rsp, unit_weight, commission_value, gst_rate)) or mrp < rsp or rsp < 0 or unit_weight < 0 or not 0 <= commission_value <= 100 or not 0 <= gst_rate <= 100:
        raise ValueError("Check product amounts: MRP >= RSP >= 0, nonnegative weight, percentage values from 0 to 100.")
    if not price_includes_gst:
        raise ValueError("Import GST-inclusive selling prices; exclusive prices are not supported.")

    return (
        sku_code,
        product_name,
        department,
        mrp,
        rsp,
        unit_weight,
        weight_unit,
        pack_multiplier,
        commission_mode,
        commission_value,
        status,
        image_url,
        brand,
        attributes_json,
        category,
        subcategory,
        hsn_code,
        gst_rate,
        price_includes_gst,
        slab_eligible,
        now_str(),
    )


def upsert_product(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO products
        (sku_code, product_name, department, mrp, rsp, unit_weight, weight_unit, pack_multiplier, commission_mode, commission_value, status, image_url, brand, attributes_json,
         category, subcategory, hsn_code, gst_rate, price_includes_gst, slab_eligible, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sku_code) DO UPDATE SET
            product_name=excluded.product_name,
            department=excluded.department,
            mrp=excluded.mrp,
            rsp=excluded.rsp,
            unit_weight=excluded.unit_weight,
            weight_unit=excluded.weight_unit,
            pack_multiplier=excluded.pack_multiplier,
            commission_mode=excluded.commission_mode,
            commission_value=excluded.commission_value,
            status=excluded.status,
            category=excluded.category,
            subcategory=excluded.subcategory,
            hsn_code=excluded.hsn_code,
            gst_rate=excluded.gst_rate,
            price_includes_gst=excluded.price_includes_gst,
            slab_eligible=excluded.slab_eligible,
            brand=CASE WHEN excluded.brand != '' THEN excluded.brand ELSE products.brand END,
            attributes_json=excluded.attributes_json,
            image_url=CASE WHEN excluded.image_url != '' THEN excluded.image_url ELSE products.image_url END
        """,
        product_values_from_row(row),
    )






TESTING_LOGIN_ACCOUNTS = {
    'FRANCHISEE': ('frn', 'Franchise', 'F'),
    'POS': ('pos', 'Outlet / POS', 'O'),
    'GUARD': ('grd', 'Guard', 'G'),
    'DELIVERY': ('dlv', 'Delivery', 'D'),
    'ADMIN': ('adm', 'Admin', 'A'),
}


def testing_login_deadline(cur) -> float:
    try:
        deadline = float(get_setting(cur, 'testing_quick_login_until', '0'))
    except (TypeError, ValueError):
        return 0
    remaining = deadline - time.time()
    return deadline if 0 < remaining <= 48 * 3600 else 0


def start_user_session(request: Request, user, testing_until: float = 0) -> RedirectResponse:
    request.session.clear()
    request.session['user_id'] = user['id']
    request.session['auth_version'] = user['auth_version']
    request.session['csrf_token'] = secrets.token_urlsafe(32)
    if testing_until:
        request.session['testing_quick_login_until'] = testing_until
    paths = {'ADMIN': '/admin/dashboard', 'FRANCHISEE': '/franchise/home',
             'POS': '/pos/home', 'GUARD': '/guard/home', 'DELIVERY': '/delivery/home'}
    return RedirectResponse(paths[user['role']], status_code=303)
























def instant_demo_topup_available():
    if os.getenv("APP_ENV", "").lower() == "production" or os.getenv("DATABASE_URL", "").strip():
        return False
    with get_conn() as conn:
        return get_setting(conn.cursor(), "test_wallet_topup_enabled", "0") == "1"








































































