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


def init_db() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                phone TEXT UNIQUE NOT NULL,
                pin TEXT NOT NULL,
                outlet_id INTEGER,
                franchisee_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS franchisees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mobile TEXT NOT NULL,
                shop_name TEXT NOT NULL,
                address TEXT,
                area TEXT,
                wallet_balance REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            );

            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku_code TEXT UNIQUE NOT NULL,
                product_name TEXT NOT NULL,
                department TEXT NOT NULL,
                mrp REAL NOT NULL,
                rsp REAL NOT NULL,
                unit_weight REAL NOT NULL DEFAULT 0,
                weight_unit TEXT NOT NULL DEFAULT 'KG',
                pack_multiplier INTEGER NOT NULL DEFAULT 1,
                commission_mode TEXT NOT NULL DEFAULT 'DEPARTMENT',
                commission_value REAL NOT NULL DEFAULT 0,
                category TEXT NOT NULL DEFAULT '',
                subcategory TEXT NOT NULL DEFAULT '',
                hsn_code TEXT NOT NULL DEFAULT '',
                gst_rate REAL NOT NULL DEFAULT 0,
                price_includes_gst INTEGER NOT NULL DEFAULT 1,
                slab_eligible INTEGER NOT NULL DEFAULT 1,
                variable_weight INTEGER NOT NULL DEFAULT 1,
                pack_label TEXT NOT NULL DEFAULT '1 kg',
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT UNIQUE NOT NULL,
                franchisee_id INTEGER NOT NULL,
                outlet_id INTEGER NOT NULL DEFAULT 1,
                delivery_type TEXT NOT NULL,
                fulfillment_mode TEXT NOT NULL,
                status TEXT NOT NULL,
                item_subtotal REAL NOT NULL,
                commission_total REAL NOT NULL,
                delivery_charge REAL NOT NULL,
                total REAL NOT NULL,
                created_at TEXT NOT NULL,
                executed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                qty INTEGER NOT NULL,
                unit_rsp REAL NOT NULL,
                line_total REAL NOT NULL,
                line_weight_kg REAL NOT NULL,
                selected_weight_kg REAL NOT NULL DEFAULT 1,
                product_name_snapshot TEXT NOT NULL DEFAULT '',
                hsn_code TEXT NOT NULL DEFAULT '',
                gst_rate REAL NOT NULL DEFAULT 0,
                taxable_value REAL NOT NULL DEFAULT 0,
                gst_amount REAL NOT NULL DEFAULT 0,
                slab_eligible INTEGER NOT NULL DEFAULT 1,
                pack_label TEXT NOT NULL DEFAULT '',
                commission_per_unit REAL NOT NULL DEFAULT 0,
                line_commission REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_number TEXT UNIQUE NOT NULL,
                order_id INTEGER NOT NULL,
                franchisee_id INTEGER NOT NULL,
                outlet_id INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'BILLED',
                expected_weight_kg REAL NOT NULL DEFAULT 0,
                actual_weight_kg REAL,
                weight_variance_kg REAL,
                weight_check_status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dockets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                docket_number TEXT UNIQUE NOT NULL,
                franchisee_id INTEGER NOT NULL,
                outlet_id INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'DOCKET_CREATED',
                expected_weight_kg REAL NOT NULL DEFAULT 0,
                actual_weight_kg REAL,
                weight_variance_kg REAL,
                weight_check_status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS docket_bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                docket_id INTEGER NOT NULL,
                bill_id INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS handover_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                from_role TEXT NOT NULL,
                to_role TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS delivery_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                franchisee_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'ASSIGNED',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wallet_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                franchisee_id INTEGER NOT NULL,
                entry_type TEXT NOT NULL,
                amount REAL NOT NULL,
                balance_after REAL NOT NULL,
                reference_type TEXT,
                reference_id INTEGER,
                remarks TEXT,
                created_by INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS earnings_wallets (
                franchisee_id INTEGER PRIMARY KEY,
                balance REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS earnings_wallet_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                franchisee_id INTEGER NOT NULL,
                entry_type TEXT NOT NULL,
                amount REAL NOT NULL,
                balance_after REAL NOT NULL,
                reference_type TEXT,
                reference_id INTEGER,
                remarks TEXT,
                created_by INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wallet_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                franchisee_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                source_wallet TEXT NOT NULL,
                destination_wallet TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'COMPLETED',
                created_by INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS target_programs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                scope TEXT NOT NULL,
                department TEXT,
                product_id INTEGER,
                metric TEXT NOT NULL DEFAULT 'SALES_AMOUNT',
                period_type TEXT NOT NULL DEFAULT 'MONTHLY',
                timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
                reward_type TEXT NOT NULL DEFAULT 'PERCENT_OF_SALES',
                starts_on TEXT,
                ends_on TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS target_slabs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                program_id INTEGER NOT NULL,
                threshold_amount REAL NOT NULL,
                reward_percent REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL,
                UNIQUE(program_id, threshold_amount)
            );

            CREATE TABLE IF NOT EXISTS franchise_target_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                franchisee_id INTEGER NOT NULL,
                program_id INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                sales_amount REAL NOT NULL DEFAULT 0,
                achieved_slab_id INTEGER,
                earned_reward REAL NOT NULL DEFAULT 0,
                credited_reward REAL NOT NULL DEFAULT 0,
                credited_at TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                updated_at TEXT NOT NULL,
                UNIQUE(franchisee_id, program_id, period_start)
            );

            CREATE TABLE IF NOT EXISTS franchise_advances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                franchisee_id INTEGER NOT NULL,
                advance_amount REAL NOT NULL,
                wallet_credit_percent REAL NOT NULL,
                wallet_credited_amount REAL NOT NULL,
                received_mode TEXT NOT NULL DEFAULT 'BANK',
                received_at TEXT NOT NULL,
                created_by INTEGER
            );

            CREATE TABLE IF NOT EXISTS franchise_bank_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                franchisee_id INTEGER NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                provider_account_ref TEXT NOT NULL DEFAULT '',
                account_holder_name TEXT NOT NULL DEFAULT '',
                bank_name TEXT NOT NULL DEFAULT '',
                account_last4 TEXT NOT NULL DEFAULT '',
                ifsc_code TEXT NOT NULL DEFAULT '',
                account_type TEXT NOT NULL DEFAULT '',
                verification_status TEXT NOT NULL DEFAULT 'PENDING',
                status TEXT NOT NULL DEFAULT 'INACTIVE',
                is_primary INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payment_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                franchisee_id INTEGER NOT NULL,
                bank_account_id INTEGER,
                transaction_type TEXT NOT NULL,
                amount_paise INTEGER NOT NULL,
                currency TEXT NOT NULL DEFAULT 'INR',
                provider TEXT NOT NULL DEFAULT '',
                provider_reference TEXT NOT NULL DEFAULT '',
                idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'CREATED',
                wallet_ledger_id INTEGER,
                failure_code TEXT NOT NULL DEFAULT '',
                failure_message TEXT NOT NULL DEFAULT '',
                initiated_by INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payment_webhook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                provider_event_id TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT '',
                payload_hash TEXT NOT NULL DEFAULT '',
                processing_status TEXT NOT NULL DEFAULT 'RECEIVED',
                received_at TEXT NOT NULL,
                processed_at TEXT,
                UNIQUE(provider, provider_event_id)
            );

            CREATE TABLE IF NOT EXISTS otp_challenges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                franchisee_id INTEGER,
                phone_e164 TEXT NOT NULL,
                purpose TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                provider_reference TEXT NOT NULL DEFAULT '',
                otp_hash TEXT NOT NULL DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                status TEXT NOT NULL DEFAULT 'PENDING',
                expires_at TEXT NOT NULL,
                verified_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS commission_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                department TEXT,
                product_id INTEGER,
                commission_type TEXT NOT NULL DEFAULT 'PERCENT',
                commission_value REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promotions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                scope TEXT NOT NULL,
                department TEXT,
                product_id INTEGER,
                discount_type TEXT NOT NULL DEFAULT 'PERCENT',
                discount_value REAL NOT NULL DEFAULT 0,
                starts_on TEXT,
                ends_on TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS order_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                notes TEXT,
                created_by INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER,
                actor_role TEXT NOT NULL,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id INTEGER,
                details TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS outlets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                code TEXT UNIQUE NOT NULL,
                address TEXT NOT NULL,
                area TEXT NOT NULL,
                phone TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_bills_order_unique ON bills(order_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_docket_bill_unique ON docket_bills(bill_id);
            CREATE INDEX IF NOT EXISTS idx_bank_accounts_franchisee ON franchise_bank_accounts(franchisee_id, status);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_bank_provider_ref ON franchise_bank_accounts(provider, provider_account_ref) WHERE provider_account_ref != '';
            CREATE INDEX IF NOT EXISTS idx_payment_franchisee ON payment_transactions(franchisee_id, created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_provider_ref ON payment_transactions(provider, provider_reference) WHERE provider_reference != '';
            CREATE INDEX IF NOT EXISTS idx_otp_phone_status ON otp_challenges(phone_e164, status, expires_at);
            CREATE INDEX IF NOT EXISTS idx_earnings_ledger_franchisee ON earnings_wallet_ledger(franchisee_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_target_progress_franchisee ON franchise_target_progress(franchisee_id, period_start);
            """
        )

        # Lightweight migrations keep existing demo databases usable.
        order_columns = {row["name"] for row in cur.execute("PRAGMA table_info(orders)").fetchall()}
        for column, definition in {
            "customer_name": "TEXT NOT NULL DEFAULT ''",
            "customer_phone": "TEXT NOT NULL DEFAULT ''",
            "customer_address": "TEXT NOT NULL DEFAULT ''",
            "distance_km": "REAL NOT NULL DEFAULT 0",
        }.items():
            if column not in order_columns:
                cur.execute(f"ALTER TABLE orders ADD COLUMN {column} {definition}")

        product_columns = {row["name"] for row in cur.execute("PRAGMA table_info(products)").fetchall()}
        if "image_url" not in product_columns:
            cur.execute("ALTER TABLE products ADD COLUMN image_url TEXT NOT NULL DEFAULT ''")

        def add_columns(table: str, columns: dict[str, str]) -> None:
            existing = {row["name"] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}
            for column, definition in columns.items():
                if column not in existing:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        add_columns("franchisees", {
            "outlet_id": "INTEGER NOT NULL DEFAULT 1",
            "delivery_address": "TEXT NOT NULL DEFAULT ''",
            "bank_account_number": "TEXT NOT NULL DEFAULT ''",
            "ifsc_code": "TEXT NOT NULL DEFAULT ''",
            "bank_name": "TEXT NOT NULL DEFAULT ''",
            "gst_number": "TEXT NOT NULL DEFAULT ''",
            "created_at": "TEXT NOT NULL DEFAULT ''",
        })
        cur.execute("UPDATE franchisees SET delivery_address=address WHERE delivery_address='' AND address!=''")
        add_columns("products", {
            "brand": "TEXT NOT NULL DEFAULT ''",
            "attributes_json": "TEXT NOT NULL DEFAULT '{}'",
            "created_at": "TEXT NOT NULL DEFAULT ''",
            "category": "TEXT NOT NULL DEFAULT ''",
            "subcategory": "TEXT NOT NULL DEFAULT ''",
            "hsn_code": "TEXT NOT NULL DEFAULT ''",
            "gst_rate": "REAL NOT NULL DEFAULT 0",
            "price_includes_gst": "INTEGER NOT NULL DEFAULT 1",
            "slab_eligible": "INTEGER NOT NULL DEFAULT 1",
            "variable_weight": "INTEGER NOT NULL DEFAULT 1",
            "pack_label": "TEXT NOT NULL DEFAULT '1 kg'",
        })
        add_columns("order_items", {
            "selected_weight_kg": "REAL NOT NULL DEFAULT 1",
            "product_name_snapshot": "TEXT NOT NULL DEFAULT ''",
            "hsn_code": "TEXT NOT NULL DEFAULT ''",
            "gst_rate": "REAL NOT NULL DEFAULT 0",
            "taxable_value": "REAL NOT NULL DEFAULT 0",
            "gst_amount": "REAL NOT NULL DEFAULT 0",
            "slab_eligible": "INTEGER NOT NULL DEFAULT 1",
            "pack_label": "TEXT NOT NULL DEFAULT ''",
            "commission_per_unit": "REAL NOT NULL DEFAULT 0",
            "line_commission": "REAL NOT NULL DEFAULT 0",
        })
        add_columns("orders", {
            "wallet_debit_amount": "REAL NOT NULL DEFAULT 0",
            "payable_to_outlet": "REAL NOT NULL DEFAULT 0",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
            "executed_at": "TEXT",
        })
        cur.execute("UPDATE orders SET executed_at=created_at WHERE (executed_at IS NULL OR executed_at='') AND status NOT IN ('ON_HOLD','CANCELLED')")
        add_columns("bills", {
            "pack_status": "TEXT NOT NULL DEFAULT 'UNPACKED'",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        })
        add_columns("dockets", {
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        })
        add_columns("handover_logs", {
            "from_user_id": "INTEGER",
            "to_user_id": "INTEGER",
            "handover_mode": "TEXT NOT NULL DEFAULT 'MANUAL'",
        })
        add_columns("delivery_tasks", {
            "assigned_user_id": "INTEGER",
            "accepted_at": "TEXT",
            "picked_at": "TEXT",
            "delivered_at": "TEXT",
            "pod_type": "TEXT",
            "pod_reference": "TEXT",
            "remarks": "TEXT",
        })

        operations.migrate(cur)
        dispatch_rules.migrate(cur)

        # Seed defaults without overwriting saved configuration.
        upsert_setting(cur, "wallet_credit_percent", "100")
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("wallet_credit_percent", "75"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("instant_premium_charge", "40"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("day_delivery_charge", "10"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("weight_tolerance_kg", "0.30"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("weight_alert_is_blocking", "0"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("delivery_per_km", "8"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("free_delivery_threshold", "999"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("free_delivery_limit_per_day", "3"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("low_wallet_threshold", "1000"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("delivery_charge_bearer", "CUSTOMER"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("bank_api_enabled", "0"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("bank_api_provider", ""))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("otp_enabled", "0"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("otp_provider", ""))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("test_wallet_topup_enabled", "1"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("business_legal_name", "Aapoorti Shopping Mall Ltd"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("business_gstin", ""))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("business_address", "Market Road, Sector 12, Bhopal, Madhya Pradesh"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("business_state_code", "23"))

        # Seed demo content only for a fresh development database.
        if os.getenv("SEED_DEMO", "0" if os.getenv("APP_ENV") == "production" else "1") == "1" and cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            if cur.execute("SELECT COUNT(1) c FROM outlets").fetchone()["c"] == 0:
                cur.executemany(
                    "INSERT INTO outlets (name, code, address, area, phone) VALUES (?, ?, ?, ?, ?)",
                    [
                        ("Aapoorti Central Hub", "OUT-01", "Market Road, Sector 12", "Sector 12", "1800-120-120"),
                        ("Aapoorti North Point", "OUT-02", "High Street, Civil Lines", "Civil Lines", "1800-120-121"),
                        ("Aapoorti East Express", "OUT-03", "Station Road, East Gate", "East Gate", "1800-120-122"),
                    ],
                )

            franchisee_count = cur.execute("SELECT COUNT(1) as c FROM franchisees").fetchone()["c"]
            if franchisee_count == 0:
                cur.execute(
                    """
                    INSERT INTO franchisees (name, mobile, shop_name, address, area, wallet_balance, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE')
                    """,
                    ("Ravi Traders", "9000000002", "Ravi Mart", "Sector 12, Market Road", "Sector 12", 5000.0),
                )

            def ensure_role_user(
                role: str,
                username: str,
                password: str,
                display_name: str,
                franchisee_id: int | None = None,
            ) -> None:
                row = cur.execute("SELECT id FROM users WHERE role=? ORDER BY id ASC LIMIT 1", (role,)).fetchone()
                if row:
                    return
                else:
                    cur.execute(
                        """
                        INSERT INTO users (name, role, phone, pin, outlet_id, franchisee_id)
                        VALUES (?, ?, ?, ?, 1, ?)
                        """,
                        (display_name, role, username, hash_password(password), franchisee_id),
                    )

            default_franchise = cur.execute("SELECT id FROM franchisees ORDER BY id ASC LIMIT 1").fetchone()
            default_franchise_id = default_franchise["id"] if default_franchise else None

            # Demo identities are created once; saved credentials are never reset.
            ensure_role_user("ADMIN", "adm", "adm@123", "Admin User")
            ensure_role_user("FRANCHISEE", "frn", "frn@123", "Franchise User", default_franchise_id)
            ensure_role_user("POS", "pos", "pos@123", "POS User")
            ensure_role_user("GUARD", "grd", "grd@123", "Guard User")
            ensure_role_user("DELIVERY", "dlv", "dlv@123", "Delivery User")

            for account in cur.execute("SELECT id,pin FROM users").fetchall():
                if not account["pin"].startswith("pbkdf2_sha256$"):
                    cur.execute("UPDATE users SET pin=? WHERE id=?", (hash_password(account["pin"]), account["id"]))

            for row in cur.execute("SELECT id, wallet_balance FROM franchisees WHERE wallet_balance != 0").fetchall():
                exists = cur.execute("SELECT 1 FROM wallet_ledger WHERE franchisee_id=? LIMIT 1", (row["id"],)).fetchone()
                if not exists:
                    cur.execute(
                        "INSERT INTO wallet_ledger (franchisee_id, entry_type, amount, balance_after, reference_type, remarks, created_at) VALUES (?, 'OPENING_BALANCE', ?, ?, 'MIGRATION', 'Opening wallet balance', ?)",
                        (row["id"], row["wallet_balance"], row["wallet_balance"], now_str()),
                    )

            product_count = cur.execute("SELECT COUNT(1) as c FROM products").fetchone()["c"]
            if product_count == 0:
                cur.executemany(
                    """
                    INSERT INTO products (sku_code, product_name, department, mrp, rsp, unit_weight, weight_unit, pack_multiplier, commission_mode, commission_value, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
                    """,
                    [
                        ("SKU-001", "Toor Dal 1kg", "Grocery", 180, 165, 1.0, "KG", 1, "PRODUCT", 4),
                        ("SKU-002", "Rice 5kg", "Grocery", 750, 699, 5.0, "KG", 1, "DEPARTMENT", 3),
                        ("SKU-003", "Noodle Pack of 5", "Snacks", 150, 135, 0.07, "KG", 5, "PRODUCT", 2),
                    ],
                )
            # Remove the earlier broad demo catalogue; the product master is intentionally curated.
            cur.execute("DELETE FROM products WHERE sku_code LIKE 'SKU-%'")

            # Curated from ALL CAT-6 BASE FILE.xlsx; hierarchy is normalized for assisted retail.
            cur.executemany(
                """INSERT OR IGNORE INTO products
                   (sku_code, product_name, department, category, subcategory, brand, hsn_code, mrp, rsp,
                    unit_weight, weight_unit, pack_multiplier, commission_mode, commission_value,
                    gst_rate, price_includes_gst, slab_eligible, status, attributes_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'KG', 1, 'PRODUCT', ?, ?, 1, ?, 'ACTIVE', '{}', ?)""",
                [
                    ("8906008819502", "Fortune Refined Soyabean Oil", "Food & Grocery", "Cooking Essentials", "Edible Oils", "Fortune", "150790", 160, 130, 2, 5, 0, now_str()),
                    ("8901262030151", "Amul Pure Ghee", "Food & Grocery", "Cooking Essentials", "Ghee", "Amul", "040590", 650, 650, 2, 5, 0, now_str()),
                    ("23", "Sugar (Loose)", "Food & Grocery", "Staples", "Sugar & Sweeteners", "Unbranded", "1701", 60, 46, 2, 5, 0, now_str()),
                    ("8901030877148", "Red Label Black Tea", "Beverages", "Tea & Infusions", "Black Tea", "Brooke Bond", "09023020", 560, 490, 8, 5, 1, now_str()),
                    ("8719514263161", "Philips Emergency LED Bulb 12W", "Home & Living", "Lighting", "Emergency LED Bulbs", "Philips", "85395000", 689, 449, 8, 18, 1, now_str()),
                    ("A23239020", "Induction Cooktop 2200W", "Electronics & Appliances", "Kitchen Appliances", "Induction Cooktops", "Generic", "8516", 4275, 3199, 8, 18, 1, now_str()),
                ],
            )
            curated_existing = [
                ("Food & Grocery", "Staples", "Rice", "10063010", 0, "35004"),
                ("Food & Grocery", "Staples", "Wheat", "1001", 0, "13098"),
                ("Food & Grocery", "Staples", "Pulses", "07139010", 0, "36042"),
            ]
            for department, category, subcategory, hsn_code, gst_rate, sku_code in curated_existing:
                cur.execute(
                    """UPDATE products SET department=?, category=?, subcategory=?, hsn_code=?, gst_rate=?, price_includes_gst=1,
                                          commission_mode='PRODUCT', commission_value=8
                       WHERE sku_code=? AND (category='' OR category IS NULL)""",
                    (department, category, subcategory, hsn_code, gst_rate, sku_code),
                )
            cur.execute(
                "UPDATE products SET commission_mode='PRODUCT', commission_value=8 WHERE sku_code IN ('35004','13098','36042')"
            )
            # Sale presentation follows the actual pack: only loose commodities can be reweighed.
            pack_profiles = [
                (1, "1 kg", "35004"), (1, "1 kg", "13098"), (1, "1 kg", "36042"),
                (0, "1 L pouch", "8906008819502"), (0, "1 L tin", "8901262030151"),
                (1, "1 kg", "23"), (0, "1 kg pouch", "8901030877148"),
                (0, "1 unit · 12W", "8719514263161"), (0, "1 unit · 2200W", "A23239020"),
            ]
            cur.executemany("UPDATE products SET variable_weight=?, pack_label=? WHERE sku_code=?", pack_profiles)
            cur.execute(
                "UPDATE products SET department='Electronics & Appliances', category='Lighting', commission_value=12 WHERE sku_code='8719514263161'"
            )
            cur.execute(
                "UPDATE products SET brand='Prestige', product_name='Prestige Induction Cooktop 2200W' WHERE sku_code='A23239020'"
            )
            cur.execute("UPDATE products SET commission_value=8 WHERE department='Food & Grocery'")
            cur.execute("UPDATE products SET commission_value=6 WHERE department='Beverages'")
            cur.execute("UPDATE products SET commission_value=12 WHERE department='Electronics & Appliances'")
            cur.execute("UPDATE products SET commission_value=2, slab_eligible=0 WHERE sku_code IN ('8906008819502','8901262030151','23')")
            department_commissions = [
                ("Food & Grocery", 8), ("Beverages", 6), ("Electronics & Appliances", 12),
            ]
            for department, rate in department_commissions:
                exists = cur.execute(
                    "SELECT 1 FROM commission_rules WHERE scope='DEPARTMENT' AND lower(department)=lower(?) AND status='ACTIVE' LIMIT 1",
                    (department,),
                ).fetchone()
                if not exists:
                    cur.execute(
                        "INSERT INTO commission_rules (scope,department,commission_type,commission_value,status,created_at) VALUES ('DEPARTMENT',?,'PERCENT',?,'ACTIVE',?)",
                        (department, rate, now_str()),
                    )
            for sku_code in ("8906008819502", "8901262030151", "23"):
                product = cur.execute("SELECT id FROM products WHERE sku_code=?", (sku_code,)).fetchone()
                if product and not cur.execute(
                    "SELECT 1 FROM commission_rules WHERE scope='PRODUCT' AND product_id=? AND status='ACTIVE' LIMIT 1", (product["id"],)
                ).fetchone():
                    cur.execute(
                        "INSERT INTO commission_rules (scope,product_id,commission_type,commission_value,status,created_at) VALUES ('PRODUCT',?,'PERCENT',2,'ACTIVE',?)",
                        (product["id"], now_str()),
                    )
            cur.execute(
                """UPDATE order_items
                   SET product_name_snapshot=COALESCE(NULLIF(product_name_snapshot,''),(SELECT product_name FROM products WHERE id=order_items.product_id),''),
                       hsn_code=COALESCE(NULLIF(hsn_code,''),(SELECT hsn_code FROM products WHERE id=order_items.product_id),''),
                       gst_rate=CASE WHEN gst_rate=0 THEN COALESCE((SELECT gst_rate FROM products WHERE id=order_items.product_id),0) ELSE gst_rate END,
                       slab_eligible=COALESCE((SELECT slab_eligible FROM products WHERE id=order_items.product_id),slab_eligible),
                       pack_label=COALESCE(NULLIF(pack_label,''),(SELECT pack_label FROM products WHERE id=order_items.product_id),'')
                   WHERE product_name_snapshot='' OR hsn_code=''"""
            )
            cur.execute(
                """UPDATE order_items
                   SET taxable_value=ROUND(CASE WHEN gst_rate>0 THEN line_total*100.0/(100.0+gst_rate) ELSE line_total END,2),
                       gst_amount=ROUND(line_total-(CASE WHEN gst_rate>0 THEN line_total*100.0/(100.0+gst_rate) ELSE line_total END),2)
                   WHERE taxable_value=0 AND line_total>0"""
            )

            cur.execute(
                """INSERT OR IGNORE INTO target_programs
                   (code, name, scope, metric, period_type, timezone, reward_type, status, created_at)
                   VALUES ('MONTHLY-TOTAL-SALES', 'Monthly Total Sales', 'TOTAL_SALE', 'SALES_AMOUNT',
                           'MONTHLY', 'Asia/Kolkata', 'PERCENT_OF_SALES', 'ACTIVE', ?)""",
                (now_str(),),
            )
            cur.execute(
                "UPDATE target_programs SET reward_type='PERCENT_OF_SALES' WHERE code='MONTHLY-TOTAL-SALES' AND reward_type='PERCENT_OF_TARGET'"
            )
            target_program = cur.execute("SELECT id FROM target_programs WHERE code='MONTHLY-TOTAL-SALES'").fetchone()
            if target_program:
                cur.executemany(
                    "INSERT OR IGNORE INTO target_slabs (program_id, threshold_amount, reward_percent, status, created_at) VALUES (?, ?, ?, 'ACTIVE', ?)",
                    [
                        (target_program["id"], 50000, 2, now_str()),
                        (target_program["id"], 100000, 4, now_str()),
                        (target_program["id"], 500000, 10, now_str()),
                    ],
                )
            for franchise_row in cur.execute("SELECT id FROM franchisees").fetchall():
                cur.execute(
                    "INSERT OR IGNORE INTO earnings_wallets (franchisee_id, balance, updated_at) VALUES (?, 0, ?)",
                    (franchise_row["id"], now_str()),
                )
                refresh_target_progress(cur, franchise_row["id"])
        for account in cur.execute("SELECT id,pin FROM users").fetchall():
            if not account["pin"].startswith("pbkdf2_sha256$"):
                cur.execute("UPDATE users SET pin=? WHERE id=?", (hash_password(account["pin"]), account["id"]))
        if os.getenv("APP_ENV") == "production":
            upsert_setting(cur, "test_wallet_topup_enabled", "0")
            for login_id in ("adm", "frn", "pos", "grd", "dlv"):
                existing_demo = cur.execute("SELECT pin FROM users WHERE phone=?", (login_id,)).fetchone()
                if existing_demo and verify_password(login_id + "@123", existing_demo["pin"]):
                    raise RuntimeError("Change all default demo passwords in development before using this database in production")
            if not cur.execute("SELECT 1 FROM users WHERE role='ADMIN'").fetchone():
                login_id, password = os.getenv("BOOTSTRAP_ADMIN_LOGIN"), os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
                if not login_id or len(password) < 12 or password.startswith("replace-"):
                    raise RuntimeError("Set BOOTSTRAP_ADMIN_LOGIN and a 12+ character BOOTSTRAP_ADMIN_PASSWORD for a new production database")
                cur.execute("INSERT INTO users(name,role,phone,pin) VALUES('Administrator','ADMIN',?,?)", (login_id, hash_password(password)))
        for f in cur.execute("SELECT id FROM franchisees").fetchall():
            cur.execute("INSERT OR IGNORE INTO earnings_wallets(franchisee_id,balance,updated_at) VALUES(?,0,?)", (f["id"], now_str()))
        conn.commit()


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


@app.get("/media/r2/{object_key:path}", include_in_schema=False)
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


@app.get("/", response_class=HTMLResponse)
def root() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None) -> HTMLResponse:
    with get_conn() as conn:
        deadline = testing_login_deadline(conn.cursor())
        accounts = []
        if deadline:
            active = {(r['phone'], r['role']) for r in conn.execute("SELECT phone,role FROM users WHERE status='ACTIVE'").fetchall()}
            accounts = [{'role': role, 'label': label, 'icon': icon}
                        for role, (phone, label, icon) in TESTING_LOGIN_ACCOUNTS.items()
                        if (phone, role) in active]
    return templates.TemplateResponse("login.html", {"request": request, "error": error, "demo_mode": os.getenv("APP_ENV") != "production" and os.getenv("SEED_DEMO", "1") == "1", "testing_accounts": accounts,
        "testing_until": datetime.fromtimestamp(deadline, IST).strftime('%d %b, %I:%M %p IST') if deadline else ''})


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


@app.post('/testing/quick-login', include_in_schema=False)
def testing_quick_login(request: Request, role: str = Form(...)) -> RedirectResponse:
    with get_conn() as conn:
        deadline = testing_login_deadline(conn.cursor())
        if not deadline or role not in TESTING_LOGIN_ACCOUNTS:
            raise HTTPException(404, 'Testing access is unavailable')
        phone = TESTING_LOGIN_ACCOUNTS[role][0]
        user = conn.execute("SELECT * FROM users WHERE phone=? AND role=? AND status='ACTIVE'", (phone, role)).fetchone()
        if not user:
            raise HTTPException(404, 'Testing account is unavailable')
        if role == 'FRANCHISEE' and not conn.execute("SELECT 1 FROM franchisees WHERE id=? AND status='ACTIVE'", (user['franchisee_id'],)).fetchone():
            raise HTTPException(403, 'Franchise is inactive')
        return start_user_session(request, user, deadline)


@app.post("/login")
def login(request: Request, phone: str = Form(...), pin: str = Form(...)) -> RedirectResponse:
    with get_conn() as conn:
        key = phone.strip().lower()
        now = time.time()
        attempts = conn.execute("SELECT COUNT(*) FROM login_attempts WHERE (login=? OR ip=?) AND created_at>?", (key, request.client.host if request.client else '', now-900)).fetchone()[0]
        if attempts >= 10:
            raise HTTPException(429, "Too many login attempts. Try again in 15 minutes.")
        row = conn.execute("SELECT * FROM users WHERE phone=? AND status='ACTIVE'", (phone.strip(),)).fetchone()
        if not row or not verify_password(pin, row["pin"]):
            conn.execute("INSERT INTO login_attempts(login,ip,created_at) VALUES (?,?,?)", (key, request.client.host if request.client else '', now))
            conn.execute("DELETE FROM login_attempts WHERE created_at<?", (now-86400,))
            return RedirectResponse("/login?error=Invalid+credentials", status_code=303)
        if row["role"] == "FRANCHISEE" and not conn.execute("SELECT 1 FROM franchisees WHERE id=? AND status='ACTIVE'", (row["franchisee_id"],)).fetchone():
            return RedirectResponse("/login?error=Account+inactive", status_code=303)
        conn.execute("DELETE FROM login_attempts WHERE login=?", (key,))
        return start_user_session(request, row)


@app.post("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, message: str | None = None, error: str | None = None):
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user

    with get_conn() as conn:
        release_matured_commissions(conn.cursor())
        conn.commit()
        stats = {
            "pending_orders": conn.execute("SELECT COUNT(*) FROM orders WHERE status NOT IN ('DELIVERED','CANCELLED','PICKED_BY_FRANCHISEE')").fetchone()[0],
            "pending_franchisees": conn.execute("SELECT COUNT(*) FROM franchisees WHERE status!='ACTIVE'").fetchone()[0],
            "franchisees": conn.execute("SELECT COUNT(1) c FROM franchisees").fetchone()["c"],
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
               ORDER BY f.id DESC LIMIT 8"""
        ).fetchall()
        products = conn.execute("SELECT * FROM products WHERE status='ACTIVE' ORDER BY id DESC LIMIT 8").fetchall()
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


@app.post("/admin/franchisees")
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
    request_key: str = Form(...),
) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
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
            (name, mobile, shop_name, address, area, wallet_balance, status, outlet_id, delivery_address, bank_account_number, ifsc_code, bank_name, gst_number, created_at)
            VALUES (?, ?, ?, ?, ?, 0, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?)
            """,
            (name.strip(), mobile.strip(), shop_name.strip(), address.strip(), area.strip(), outlet_id,
             (delivery_address.strip() or address.strip()), bank_account_number.strip(), ifsc_code.strip().upper(), bank_name.strip(), gst_number.strip().upper(), now_str()),
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


@app.post("/admin/products")
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


@app.post("/admin/products/image")
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


@app.post("/admin/products/import")
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


@app.post("/admin/settings")
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


@app.post("/admin/tax-profile")
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


@app.post("/admin/franchisees/{franchisee_id}/wallet")
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


def instant_demo_topup_available():
    if os.getenv("APP_ENV", "").lower() == "production" or os.getenv("DATABASE_URL", "").strip():
        return False
    with get_conn() as conn:
        return get_setting(conn.cursor(), "test_wallet_topup_enabled", "0") == "1"


@app.post("/franchise/demo-topups")
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


@app.post("/admin/franchisees/{franchisee_id}/test-wallet-topup")
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


@app.post("/admin/franchisees/{franchisee_id}/advance")
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


@app.post("/admin/franchisees/{franchisee_id}/status")
def change_franchise_status(franchisee_id: int, request: Request, status: str = Form(...)) -> RedirectResponse:
    user = require_role(request, "ADMIN")
    if isinstance(user, RedirectResponse):
        return user
    status = "ACTIVE" if status == "ACTIVE" else "INACTIVE"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE franchisees SET status=? WHERE id=?", (status, franchisee_id))
        audit(cur, user, "STATUS_CHANGE", "FRANCHISEE", franchisee_id, status)
        conn.commit()
    return RedirectResponse("/admin/dashboard?message=Franchisee+status+updated#franchisees", status_code=303)


@app.post("/admin/outlets")
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


@app.post("/admin/franchisees/{franchisee_id}/delivery-address")
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


@app.post("/admin/users")
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


@app.post("/admin/commissions")
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


@app.post("/admin/orders/{order_id}/status")
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


@app.post("/admin/orders/{order_id}/returns")
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


@app.post("/admin/promotions")
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


@app.get("/franchise/home", response_class=HTMLResponse)
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
        outlet = conn.execute("SELECT * FROM outlets WHERE id=?", (user["outlet_id"] or 1,)).fetchone()
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


@app.get("/franchise/bills/{bill_id}", response_class=HTMLResponse)
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


@app.get("/franchise/summary.pdf")
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


@app.post("/franchise/earnings/transfer")
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


@app.get("/franchise/customers/list")
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


@app.get("/franchise/customers/{phone}/details")
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


@app.post("/franchise/customers")
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


@app.post("/franchise/orders/{order_id}/cancel")
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


@app.post("/franchise/orders/{order_id}/customer-handover")
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


@app.post("/franchise/orders/{order_id}/execute")
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


@app.post("/franchise/orders")
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

        outlet_id = user["outlet_id"] or 1
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


@app.get("/pos/home", response_class=HTMLResponse)
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


@app.post("/pos/bills")
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


@app.post("/pos/dockets")
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


@app.post("/pos/handover")
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


@app.post("/pos/bills/{bill_id}/pack")
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


@app.get("/guard/home", response_class=HTMLResponse)
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


@app.post("/guard/weight-check")
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


@app.post("/guard/handover")
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


@app.get("/delivery/home", response_class=HTMLResponse)
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


@app.post("/delivery/tasks")
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


@app.get("/api/summary", response_model=None)
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


@app.get("/ready")
def readiness():
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM settings LIMIT 1").fetchone()
        return {"status": "ready"}
    except sqlite3.Error:
        raise HTTPException(503, "Database unavailable")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": now_str()}


operations.register(__import__(__name__, fromlist=["app"]))

dispatch_rules.register(__import__(__name__, fromlist=["app"]))
from app import mobile_api
mobile_api.register(__import__(__name__, fromlist=["app"]))
