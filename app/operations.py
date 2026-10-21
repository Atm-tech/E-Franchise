"""Operational controls, provider boundaries, and role-scoped workspaces."""
import hashlib
import hmac
import io
import json
import math
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus, urlsplit, urlunsplit
from uuid import uuid4

from fastapi import Form, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response, FileResponse, JSONResponse

from app import providers
from app.security import hash_password, verify_password

a = None


def migrate(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS franchise_customer_profiles (
        franchisee_id INTEGER NOT NULL REFERENCES franchisees(id), phone TEXT NOT NULL,
        name TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
        PRIMARY KEY(franchisee_id,phone))""")
    columns = {
        'franchise_customer_profiles': {'address': "TEXT NOT NULL DEFAULT ''"},
        'users': {'status': "TEXT NOT NULL DEFAULT 'ACTIVE'", 'auth_version': 'INTEGER NOT NULL DEFAULT 1'},
        'franchisees': {'state_code': "TEXT NOT NULL DEFAULT '23'", 'latitude': 'REAL', 'longitude': 'REAL', 'route_distance_km': 'REAL', 'route_outlet_id': 'INTEGER'},
        'outlets': {'latitude': 'REAL', 'longitude': 'REAL'},
        'orders': {'buyer_address': "TEXT NOT NULL DEFAULT ''", 'request_key': 'TEXT', 'supply_state_code': "TEXT NOT NULL DEFAULT '23'", 'supplier_state_code': "TEXT NOT NULL DEFAULT '23'", 'wallet_required': 'REAL', 'delivery_cost': 'REAL', 'delivery_charge_bearer': "TEXT NOT NULL DEFAULT 'CUSTOMER'", 'return_window_days': 'INTEGER NOT NULL DEFAULT 4', 'delivered_at': 'TEXT', 'commission_available_at': 'TEXT', 'commission_released_at': 'TEXT'},
        'commission_rules': {'starts_on': 'TEXT', 'ends_on': 'TEXT'},
        'delivery_tasks': {'failed_from': 'TEXT', 'failure_reason': 'TEXT', 'latitude': 'REAL', 'longitude': 'REAL', 'location_at': 'TEXT'},
        'payment_transactions': {'payment_id': 'TEXT', 'verified_at': 'TEXT'},
    }
    for table, additions in columns.items():
        existing = {r['name'] for r in cur.execute(f'PRAGMA table_info({table})')}
        for key, definition in additions.items():
            if key not in existing:
                cur.execute(f'ALTER TABLE {table} ADD COLUMN {key} {definition}')
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS login_attempts(id INTEGER PRIMARY KEY, login TEXT, ip TEXT, created_at REAL);
        CREATE TABLE IF NOT EXISTS mutation_keys(user_id INTEGER NOT NULL, action TEXT NOT NULL, request_key TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(user_id,action,request_key));
        CREATE INDEX IF NOT EXISTS idx_login_attempts_time ON login_attempts(created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_order_request ON orders(franchisee_id, request_key) WHERE request_key IS NOT NULL;
        CREATE TABLE IF NOT EXISTS pick_sessions(order_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, expires_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS picked_items(order_item_id INTEGER PRIMARY KEY, qty INTEGER NOT NULL DEFAULT 0, user_id INTEGER, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS proofs(id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, path TEXT NOT NULL, mime TEXT NOT NULL, kind TEXT NOT NULL, created_by INTEGER, created_at TEXT);
        CREATE TABLE IF NOT EXISTS settlements(id INTEGER PRIMARY KEY, outlet_id INTEGER NOT NULL, total REAL NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', payment_reference TEXT, created_by INTEGER, created_at TEXT, paid_at TEXT);
        CREATE TABLE IF NOT EXISTS settlement_orders(settlement_id INTEGER NOT NULL, order_id INTEGER NOT NULL UNIQUE, amount REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS order_returns(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, order_item_id INTEGER NOT NULL, quantity INTEGER NOT NULL, commission_reversed REAL NOT NULL, reason TEXT NOT NULL, created_by INTEGER, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_order_returns_order_item ON order_returns(order_item_id,id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_order_commission_credit ON earnings_wallet_ledger(franchisee_id,reference_id) WHERE reference_type='ORDER_COMMISSION';
        CREATE TABLE IF NOT EXISTS config_versions(id INTEGER PRIMARY KEY, setting_key TEXT NOT NULL, old_value TEXT, new_value TEXT, changed_at TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS record_setting_version AFTER UPDATE ON settings WHEN OLD.value != NEW.value BEGIN
            INSERT INTO config_versions(setting_key, old_value, new_value, changed_at) VALUES(NEW.key, OLD.value, NEW.value, datetime('now'));
        END;
        CREATE TABLE IF NOT EXISTS device_readings(id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE, outlet_id INTEGER NOT NULL, entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL, weight_kg REAL, item_count INTEGER, created_at TEXT NOT NULL, applied_at TEXT);
        CREATE TABLE IF NOT EXISTS inventory_balances(outlet_id INTEGER NOT NULL, product_id INTEGER NOT NULL, on_hand INTEGER NOT NULL DEFAULT 0 CHECK(on_hand>=0), reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved>=0 AND reserved<=on_hand), updated_at TEXT NOT NULL, PRIMARY KEY(outlet_id,product_id));
        CREATE TABLE IF NOT EXISTS inventory_movements(id INTEGER PRIMARY KEY, outlet_id INTEGER NOT NULL, product_id INTEGER NOT NULL, kind TEXT NOT NULL, quantity_delta INTEGER NOT NULL, reserved_delta INTEGER NOT NULL DEFAULT 0, reference TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', created_by INTEGER, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_inventory_movement_product ON inventory_movements(outlet_id,product_id,id DESC);
        CREATE TRIGGER IF NOT EXISTS immutable_inventory_update BEFORE UPDATE ON inventory_movements BEGIN SELECT RAISE(ABORT,'Inventory ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_inventory_delete BEFORE DELETE ON inventory_movements BEGIN SELECT RAISE(ABORT,'Inventory ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_wallet_update BEFORE UPDATE ON wallet_ledger BEGIN SELECT RAISE(ABORT,'Wallet ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_wallet_delete BEFORE DELETE ON wallet_ledger BEGIN SELECT RAISE(ABORT,'Wallet ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_earnings_update BEFORE UPDATE ON earnings_wallet_ledger BEGIN SELECT RAISE(ABORT,'Earnings ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_earnings_delete BEFORE DELETE ON earnings_wallet_ledger BEGIN SELECT RAISE(ABORT,'Earnings ledger is append-only'); END;
    ''')
    if getattr(cur, 'is_postgres', False):
        cur.execute("""CREATE OR REPLACE FUNCTION reject_immutable_ledger_change() RETURNS trigger
                     LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Wallet ledger is append-only'; END $$""")
        for table in ('wallet_ledger', 'earnings_wallet_ledger'):
            for operation in ('UPDATE', 'DELETE'):
                trigger = f'block_{table}_{operation.lower()}'
                cur.execute(f'DROP TRIGGER IF EXISTS {trigger} ON {table}')
                cur.execute(f'CREATE TRIGGER {trigger} BEFORE {operation} ON {table} FOR EACH ROW EXECUTE FUNCTION reject_immutable_ledger_change()')
        for operation in ('UPDATE', 'DELETE'):
            trigger = f'block_inventory_movements_{operation.lower()}'
            cur.execute(f'DROP TRIGGER IF EXISTS {trigger} ON inventory_movements')
            cur.execute(f'CREATE TRIGGER {trigger} BEFORE {operation} ON inventory_movements FOR EACH ROW EXECUTE FUNCTION reject_immutable_ledger_change()')
        cur.execute("""CREATE OR REPLACE FUNCTION record_setting_version() RETURNS trigger
                     LANGUAGE plpgsql AS $$ BEGIN
                     IF OLD.value IS DISTINCT FROM NEW.value THEN
                       INSERT INTO config_versions(setting_key, old_value, new_value, changed_at)
                       VALUES(NEW.key, OLD.value, NEW.value, CURRENT_TIMESTAMP::text);
                     END IF; RETURN NEW; END $$""")
        cur.execute('DROP TRIGGER IF EXISTS record_setting_version ON settings')
        cur.execute('CREATE TRIGGER record_setting_version AFTER UPDATE ON settings FOR EACH ROW EXECUTE FUNCTION record_setting_version()')
    cur.execute('BEGIN IMMEDIATE')
    cur.execute('UPDATE orders SET wallet_required=total WHERE wallet_required IS NULL')
    cur.execute('UPDATE orders SET delivery_cost=delivery_charge WHERE delivery_cost IS NULL')
    cur.execute('PRAGMA user_version=2')
    for key, value in {'require_picking': '1', 'geofence_radius_m': '0', 'max_bills_per_docket': '30', 'weight_tolerance_percent': '0', 'customer_return_window_days': '4'}.items():
        cur.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (key, value))


def require_user(request, roles=None):
    user = a.current_user(request)
    if not user:
        raise HTTPException(401, 'Please sign in')
    if roles and user['role'] not in roles:
        raise HTTPException(403, 'This action is not available for your role')
    return user


def claim_mutation(cur,user,action,key):
    if not key: return True  # Older API callers remain supported; browser forms always supply a key.
    if len(key)>100: raise HTTPException(400,'Invalid request key')
    return cur.execute('INSERT OR IGNORE INTO mutation_keys VALUES(?,?,?,?)',(user['id'],action,key,a.now_str())).rowcount==1


def redirect(message, target='/workspace'):
    parts = urlsplit(target)
    query = (parts.query + '&' if parts.query else '') + 'message=' + quote_plus(message)
    return RedirectResponse(urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment)), status_code=303)


def positive(value, allow_zero=False):
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        raise HTTPException(400, 'Enter a valid positive amount')
    return round(value, 2)


def entity(cur, kind, eid):
    if kind not in {'BILL', 'DOCKET'}:
        raise HTTPException(400, 'Invalid dispatch type')
    row = cur.execute(f"SELECT * FROM {'bills' if kind == 'BILL' else 'dockets'} WHERE id=?", (eid,)).fetchone()
    if not row:
        raise HTTPException(404, 'Dispatch not found')
    return row


def require_picked(cur, order, user):
    if a.get_setting(cur, 'require_picking', '1') != '1':
        return
    lease = cur.execute('SELECT * FROM pick_sessions WHERE order_id=?', (order['id'],)).fetchone()
    if not lease or lease['user_id'] != user['id'] or lease['expires_at'] < time.time():
        raise HTTPException(409, 'Claim the order in Operations before billing')
    missing = cur.execute('SELECT 1 FROM order_items oi LEFT JOIN picked_items p ON p.order_item_id=oi.id WHERE oi.order_id=? AND COALESCE(p.qty,0)!=oi.qty', (order['id'],)).fetchone()
    if missing:
        raise HTTPException(409, 'Scan and pick every ordered item before billing')


def reserve_order_inventory(cur, order_id, outlet_id, user_id):
    items = cur.execute('SELECT product_id, SUM(qty) qty FROM order_items WHERE order_id=? GROUP BY product_id', (order_id,)).fetchall()
    billed = cur.execute("SELECT 1 FROM inventory_movements WHERE reference=? AND kind='SALE' LIMIT 1", (f'ORDER:{order_id}',)).fetchone()
    if billed:
        return
    for item in items:
        balance = cur.execute('SELECT on_hand,reserved FROM inventory_balances WHERE outlet_id=? AND product_id=?', (outlet_id,item['product_id'])).fetchone()
        # ON_HOLD rows have no reservation yet; an existing reservation is
        # reusable when an execution is retried after its database commit.
        reserved = int(balance['reserved']) if balance else 0
        available = int(balance['on_hand']-reserved) if balance else 0
        existing_reservation = cur.execute("SELECT COALESCE(SUM(reserved_delta),0) qty FROM inventory_movements WHERE outlet_id=? AND product_id=? AND reference=? AND kind='RESERVE'", (outlet_id,item['product_id'],f'ORDER:{order_id}')).fetchone()['qty']
        existing_release = cur.execute("SELECT COALESCE(SUM(-reserved_delta),0) qty FROM inventory_movements WHERE outlet_id=? AND product_id=? AND reference=? AND kind='RELEASE'", (outlet_id,item['product_id'],f'ORDER:{order_id}')).fetchone()['qty']
        sales = cur.execute("SELECT COALESCE(SUM(-quantity_delta),0) qty FROM inventory_movements WHERE outlet_id=? AND product_id=? AND reference=? AND kind='SALE'", (outlet_id,item['product_id'],f'ORDER:{order_id}')).fetchone()['qty']
        outstanding = max(0, int(item['qty'])-int(sales))
        if outstanding == 0:
            continue
        need_to_reserve = max(0, outstanding - int(existing_reservation) + int(existing_release))
        if balance and int(balance['reserved']) >= int(item['qty'])-int(sales) and int(existing_reservation)>int(existing_release):
            need_to_reserve = 0
        if need_to_reserve > available:
            product = cur.execute('SELECT product_name FROM products WHERE id=?',(item['product_id'],)).fetchone()
            raise HTTPException(409, f"Not enough stock for {product['product_name'] if product else 'product'}: {available} available")
    for item in items:
        balance = cur.execute('SELECT reserved FROM inventory_balances WHERE outlet_id=? AND product_id=?', (outlet_id,item['product_id'])).fetchone()
        existing_reservation = int(cur.execute("SELECT COALESCE(SUM(reserved_delta),0) qty FROM inventory_movements WHERE outlet_id=? AND product_id=? AND reference=? AND kind='RESERVE'", (outlet_id,item['product_id'],f'ORDER:{order_id}')).fetchone()['qty'])
        existing_release = int(cur.execute("SELECT COALESCE(SUM(-reserved_delta),0) qty FROM inventory_movements WHERE outlet_id=? AND product_id=? AND reference=? AND kind='RELEASE'", (outlet_id,item['product_id'],f'ORDER:{order_id}')).fetchone()['qty'])
        sales = int(cur.execute("SELECT COALESCE(SUM(-quantity_delta),0) qty FROM inventory_movements WHERE outlet_id=? AND product_id=? AND reference=? AND kind='SALE'", (outlet_id,item['product_id'],f'ORDER:{order_id}')).fetchone()['qty'])
        if sales >= int(item['qty']):
            continue
        delta = max(0, int(item['qty'])-sales - existing_reservation + existing_release)
        if balance and int(balance['reserved']) >= int(item['qty'])-sales and existing_reservation>existing_release:
            continue
        if delta:
            cur.execute('UPDATE inventory_balances SET reserved=reserved+?,updated_at=? WHERE outlet_id=? AND product_id=?', (delta, a.now_str(), outlet_id,item['product_id']))
            cur.execute("INSERT INTO inventory_movements(outlet_id,product_id,kind,quantity_delta,reserved_delta,reference,reason,created_by,created_at) VALUES(?,?,'RESERVE',0,?,?,?, ?,?)", (outlet_id,item['product_id'],delta,f'ORDER:{order_id}','Order confirmed',user_id,a.now_str()))


def release_order_inventory(cur, order_id, outlet_id, user_id, reason):
    items = cur.execute('SELECT product_id,SUM(qty) qty FROM order_items WHERE order_id=? GROUP BY product_id',(order_id,)).fetchall()
    for item in items:
        released = int(cur.execute("SELECT COALESCE(SUM(-reserved_delta),0) qty FROM inventory_movements WHERE outlet_id=? AND product_id=? AND reference=? AND kind='RELEASE'",(outlet_id,item['product_id'],f'ORDER:{order_id}')).fetchone()['qty'])
        reserved_by_order = int(cur.execute("SELECT COALESCE(SUM(reserved_delta),0) qty FROM inventory_movements WHERE outlet_id=? AND product_id=? AND reference=? AND kind='RESERVE'",(outlet_id,item['product_id'],f'ORDER:{order_id}')).fetchone()['qty'])
        already_sold = int(cur.execute("SELECT COALESCE(SUM(-quantity_delta),0) qty FROM inventory_movements WHERE outlet_id=? AND product_id=? AND reference=? AND kind='SALE'",(outlet_id,item['product_id'],f'ORDER:{order_id}')).fetchone()['qty'])
        release_qty = max(0,min(int(item['qty'])-already_sold-released,reserved_by_order-released))
        if not release_qty:
            continue
        balance = cur.execute('SELECT reserved FROM inventory_balances WHERE outlet_id=? AND product_id=?',(outlet_id,item['product_id'])).fetchone()
        if not balance or int(balance['reserved']) < release_qty:
            raise HTTPException(409,'Inventory reservation is inconsistent; review inventory movements before cancelling')
        cur.execute('UPDATE inventory_balances SET reserved=reserved-?,updated_at=? WHERE outlet_id=? AND product_id=?',(release_qty,a.now_str(),outlet_id,item['product_id']))
        cur.execute("INSERT INTO inventory_movements(outlet_id,product_id,kind,quantity_delta,reserved_delta,reference,reason,created_by,created_at) VALUES(?,?,'RELEASE',0,?,?,?, ?,?)",(outlet_id,item['product_id'],-release_qty,f'ORDER:{order_id}',reason,user_id,a.now_str()))


def consume_order_inventory(cur, order_id, outlet_id, user_id):
    items = cur.execute('SELECT product_id,SUM(qty) qty FROM order_items WHERE order_id=? GROUP BY product_id',(order_id,)).fetchall()
    for item in items:
        already_sold = int(cur.execute("SELECT COALESCE(SUM(-quantity_delta),0) qty FROM inventory_movements WHERE outlet_id=? AND product_id=? AND reference=? AND kind='SALE'",(outlet_id,item['product_id'],f'ORDER:{order_id}')).fetchone()['qty'])
        if already_sold >= int(item['qty']):
            continue
        balance=cur.execute('SELECT on_hand,reserved FROM inventory_balances WHERE outlet_id=? AND product_id=?',(outlet_id,item['product_id'])).fetchone()
        outstanding = int(item['qty'])-already_sold
        if not balance or int(balance['reserved'])<outstanding or int(balance['on_hand'])<outstanding:
            raise HTTPException(409,'Reserved stock is inconsistent; cannot bill this order')
    for item in items:
        already_sold = int(cur.execute("SELECT COALESCE(SUM(-quantity_delta),0) qty FROM inventory_movements WHERE outlet_id=? AND product_id=? AND reference=? AND kind='SALE'",(outlet_id,item['product_id'],f'ORDER:{order_id}')).fetchone()['qty'])
        outstanding = int(item['qty'])-already_sold
        if outstanding <= 0:
            continue
        cur.execute('UPDATE inventory_balances SET on_hand=on_hand-?,reserved=reserved-?,updated_at=? WHERE outlet_id=? AND product_id=?',(outstanding,outstanding,a.now_str(),outlet_id,item['product_id']))
        cur.execute("INSERT INTO inventory_movements(outlet_id,product_id,kind,quantity_delta,reserved_delta,reference,reason,created_by,created_at) VALUES(?,?,'SALE',?,?,?,'Billed order',?,?)",(outlet_id,item['product_id'],-outstanding,-outstanding,f'ORDER:{order_id}',user_id,a.now_str()))


def receive_inventory(request: Request, outlet_id: int = Form(...), product_id: int = Form(...), quantity: int = Form(...), reference: str = Form(''), reason: str = Form(''), request_key: str = Form(...)):
    user = require_user(request, {'ADMIN'})
    if quantity <= 0 or quantity > 1000000 or len(reason.strip()) < 3:
        raise HTTPException(400, 'Enter a positive quantity and a short reason')
    with a.get_conn() as conn:
        cur=conn.cursor()
        if not claim_mutation(cur,user,'INVENTORY_RECEIPT',request_key):
            return redirect('This stock receipt was already recorded.', '/admin/dashboard#catalog')
        if not cur.execute('SELECT 1 FROM outlets WHERE id=? AND status=\'ACTIVE\'',(outlet_id,)).fetchone() or not cur.execute('SELECT 1 FROM products WHERE id=? AND status=\'ACTIVE\'',(product_id,)).fetchone():
            raise HTTPException(404,'Choose an active outlet and product')
        cur.execute('INSERT INTO inventory_balances(outlet_id,product_id,on_hand,reserved,updated_at) VALUES(?,?,?,0,?) ON CONFLICT(outlet_id,product_id) DO UPDATE SET on_hand=inventory_balances.on_hand+excluded.on_hand,updated_at=excluded.updated_at',(outlet_id,product_id,quantity,a.now_str()))
        cur.execute("INSERT INTO inventory_movements(outlet_id,product_id,kind,quantity_delta,reserved_delta,reference,reason,created_by,created_at) VALUES(?,?,'RECEIPT',?,0,?,?,?,?)",(outlet_id,product_id,quantity,reference.strip() or request_key,reason.strip(),user['id'],a.now_str()))
        a.audit(cur,user,'STOCK_RECEIVED','PRODUCT',product_id,f'outlet={outlet_id}; quantity={quantity}; reference={reference.strip()}')
    return redirect('Stock received and logged.', '/admin/dashboard#catalog')


def otp_digest(value):
    return hmac.new(a.session_secret(a.DATA_DIR).encode(), value.encode(), hashlib.sha256).hexdigest()


def verify_code(cur, purpose, fid, code):
    row = cur.execute("SELECT * FROM otp_challenges WHERE franchisee_id=? AND purpose=? AND status='PENDING' ORDER BY id DESC LIMIT 1", (fid, purpose)).fetchone()
    if not row or row['expires_at'] < a.now_str() or row['attempts'] >= row['max_attempts']:
        raise HTTPException(400, 'Code is missing, expired, or locked. Franchisee must generate a new code.')
    if not hmac.compare_digest(row['otp_hash'], otp_digest(code.strip())):
        cur.execute('UPDATE otp_challenges SET attempts=attempts+1 WHERE id=?', (row['id'],))
        cur.connection.commit()  # Failed attempts must survive the validation error.
        raise HTTPException(400, 'Incorrect handover code')
    cur.execute("UPDATE otp_challenges SET status='VERIFIED', verified_at=? WHERE id=?", (a.now_str(), row['id']))


def validate_release(cur, row, kind, target, code, user):
    if kind=='BILL' and cur.execute('SELECT 1 FROM docket_bills WHERE bill_id=?',(row['id'],)).fetchone():
        raise HTTPException(409,'Release the complete docket, not a grouped bill')
    if row['status'] not in {'WEIGHT_CHECK_PASSED', 'WEIGHT_CHECK_ALERT'}:
        raise HTTPException(409, 'Dispatch has already left the guard or is not ready')
    if row['seal_status'] == 'BROKEN' or (a.get_setting(cur, 'require_seal_check', '1') == '1' and row['seal_status'] != 'INTACT'):
        raise HTTPException(409, 'Inspect and confirm an intact seal before release')
    if row['weight_check_status'] == 'PENDING' or (a.get_setting(cur, 'weight_alert_is_blocking', '0') == '1' and row['weight_check_status'] == 'ALERT'):
        raise HTTPException(409, 'Complete a passing weight check before releasing dispatch')
    ids = a.order_ids_for_entity(cur, kind, row['id'])
    modes = {r[0] for r in cur.execute(f"SELECT fulfillment_mode FROM orders WHERE id IN ({','.join('?' for _ in ids)})", ids)}
    expected = 'FRANCHISEE' if modes == {'FRANCHISE_PICKUP'} else 'DELIVERY'
    if target != expected:
        raise HTTPException(400, 'Receiver must match the selected fulfillment mode')
    if target == 'FRANCHISEE':
        verify_code(cur, f'PICKUP:{kind}:{row["id"]}', row['franchisee_id'], code)


def verify_completion(cur, task, kind, code, user):
    if kind not in {'OTP', 'PHOTO', 'SIGNATURE'}:
        raise HTTPException(400, 'Invalid delivery proof type')
    if kind != 'OTP' and not cur.execute('SELECT 1 FROM proofs WHERE task_id=? AND kind=?', (task['id'], kind)).fetchone():
        raise HTTPException(400, 'Upload the photo or signature first')
    radius = float(a.get_setting(cur, 'geofence_radius_m', '0'))
    if radius > 0:
        f = cur.execute('SELECT latitude,longitude FROM franchisees WHERE id=?', (task['franchisee_id'],)).fetchone()
        if None in (f['latitude'], f['longitude'], task['latitude'], task['longitude']) or not task['location_at'] or task['location_at'] < (datetime.now(a.IST)-timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S'):
            raise HTTPException(400, 'Capture a current delivery location; franchise coordinates must be configured')
        if distance_km(f['latitude'], f['longitude'], task['latitude'], task['longitude']) * 1000 > radius:
            raise HTTPException(400, 'Outside the configured delivery geofence')
    verify_code(cur, f'DELIVERY:{task["id"]}', task['franchisee_id'], code)


def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = p2-p1, math.radians(lon2-lon1)
    value = math.sin(dlat/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlon/2)**2
    return round(6371 * 2 * math.asin(min(1, math.sqrt(value))), 3)


def register(main):
    global a
    a = main
    for route, method, handler in ROUTES:
        a.app.add_api_route(route, handler, methods=[method], response_model=None)
    a.templates.env.globals['new_request_key'] = lambda: uuid4().hex
    a.app.add_exception_handler(HTTPException, http_error)
    a.app.add_exception_handler(sqlite3.IntegrityError, integrity_error)


async def http_error(request,exc):
    if 'text/html' in request.headers.get('accept','') and not request.url.path.startswith(('/api/','/integrations/')):
        return a.templates.TemplateResponse('error.html',{'request':request,'user':a.current_user(request),'status':exc.status_code,'detail':exc.detail},status_code=exc.status_code)
    return JSONResponse({'detail':exc.detail},status_code=exc.status_code,headers=exc.headers)


async def integrity_error(request,exc):
    return await http_error(request,HTTPException(409,'This record conflicts with an existing operation. Refresh and review its status.'))


def account(request: Request):
    user = require_user(request)
    return a.templates.TemplateResponse('account.html', {'request': request, 'user': user})


def password_change(request: Request, current_password: str = Form(...), new_password: str = Form(..., min_length=8, max_length=128)):
    user = require_user(request)
    if not verify_password(current_password, user['pin']):
        raise HTTPException(400, 'Current password is incorrect')
    with a.get_conn() as conn:
        conn.execute('UPDATE users SET pin=?,auth_version=auth_version+1 WHERE id=?', (hash_password(new_password), user['id']))
        a.audit(conn.cursor(), user, 'PASSWORD_CHANGE', 'USER', user['id'])
    request.session.clear()
    return redirect('Password changed. Sign in again.', '/login')


def workspace(request: Request):
    user = require_user(request)
    with a.get_conn() as conn:
        cur = conn.cursor()
        data = {'request': request, 'user': user, 'message': request.query_params.get('message', ''), 'payment_ready': providers.payment_ready()}
        if user['role'] == 'ADMIN':
            queries = {
                'outlets': 'SELECT * FROM outlets ORDER BY id',
                'partners': 'SELECT id,shop_name,state_code,latitude,longitude,route_distance_km,delivery_mode FROM franchisees ORDER BY id',
                'staff': "SELECT id,name,role,status,outlet_id FROM users ORDER BY id",
                'programs': 'SELECT * FROM target_programs ORDER BY id',
                'slabs': 'SELECT s.*,p.name FROM target_slabs s JOIN target_programs p ON p.id=s.program_id ORDER BY p.id,s.threshold_amount',
                'settlements': 'SELECT s.*,o.name outlet_name FROM settlements s JOIN outlets o ON o.id=s.outlet_id ORDER BY s.id DESC LIMIT 100',
                'payments': 'SELECT t.*,f.shop_name FROM payment_transactions t JOIN franchisees f ON f.id=t.franchisee_id ORDER BY t.id DESC LIMIT 100',
                'tasks': "SELECT t.*,f.shop_name FROM delivery_tasks t JOIN franchisees f ON f.id=t.franchisee_id ORDER BY t.id DESC LIMIT 100",
                'versions': 'SELECT * FROM config_versions ORDER BY id DESC LIMIT 30',
                'audits': 'SELECT * FROM audit_logs ORDER BY id DESC LIMIT 50',
                'banks': 'SELECT b.*,f.shop_name FROM franchise_bank_accounts b JOIN franchisees f ON f.id=b.franchisee_id ORDER BY b.id DESC',
            }
            data.update({k: cur.execute(q).fetchall() for k,q in queries.items()})
            data['settings'] = {r['key']:r['value'] for r in cur.execute('SELECT * FROM settings')}
            data['pending_settlements'] = cur.execute("SELECT o.outlet_id,x.name,COUNT(*) orders,ROUND(SUM(o.payable_to_outlet),2) amount FROM orders o JOIN outlets x ON x.id=o.outlet_id WHERE o.status='DELIVERED' AND o.id NOT IN (SELECT order_id FROM settlement_orders) GROUP BY o.outlet_id,x.name").fetchall()
            data['proofs']=cur.execute('SELECT id,task_id,kind FROM proofs ORDER BY id DESC').fetchall()
        elif user['role'] == 'POS':
            data['picking_required'] = a.get_setting(cur, 'require_picking', '1') == '1'
            data['orders'] = cur.execute("SELECT o.*,p.user_id picker_id,u.name picker_name,p.expires_at pick_expires_at,(SELECT COALESCE(SUM(oi.qty),0) FROM order_items oi WHERE oi.order_id=o.id) item_count,(SELECT COALESCE(SUM(pi.qty),0) FROM order_items oi LEFT JOIN picked_items pi ON pi.order_item_id=oi.id WHERE oi.order_id=o.id) picked_count FROM orders o LEFT JOIN pick_sessions p ON p.order_id=o.id LEFT JOIN users u ON u.id=p.user_id WHERE o.outlet_id=? AND o.status='EXECUTED' AND o.id NOT IN (SELECT order_id FROM bills) ORDER BY o.id", (user['outlet_id'],)).fetchall()
            data['items'] = cur.execute('SELECT oi.*,p.sku_code,COALESCE(pi.qty,0) picked FROM order_items oi JOIN orders o ON o.id=oi.order_id JOIN products p ON p.id=oi.product_id LEFT JOIN picked_items pi ON pi.order_item_id=oi.id WHERE o.outlet_id=?', (user['outlet_id'],)).fetchall()
            data['bills'] = cur.execute('SELECT * FROM bills WHERE outlet_id=? ORDER BY id DESC LIMIT 50', (user['outlet_id'],)).fetchall()
            data['dockets'] = cur.execute('SELECT * FROM dockets WHERE outlet_id=? ORDER BY id DESC LIMIT 50', (user['outlet_id'],)).fetchall()
        elif user['role'] == 'FRANCHISEE':
            data['bills'] = cur.execute("SELECT b.* FROM bills b JOIN orders o ON o.id=b.order_id WHERE b.franchisee_id=? AND o.fulfillment_mode='FRANCHISE_PICKUP' AND b.status IN ('WEIGHT_CHECK_PASSED','WEIGHT_CHECK_ALERT') AND b.id NOT IN (SELECT bill_id FROM docket_bills)", (user['franchisee_id'],)).fetchall()
            data['dockets'] = cur.execute("SELECT d.* FROM dockets d WHERE d.franchisee_id=? AND d.status IN ('WEIGHT_CHECK_PASSED','WEIGHT_CHECK_ALERT') AND EXISTS(SELECT 1 FROM docket_bills db JOIN bills b ON b.id=db.bill_id JOIN orders o ON o.id=b.order_id WHERE db.docket_id=d.id AND o.fulfillment_mode='FRANCHISE_PICKUP')", (user['franchisee_id'],)).fetchall()
            data['tasks'] = cur.execute("SELECT * FROM delivery_tasks WHERE franchisee_id=? AND status='OUT_FOR_DELIVERY'", (user['franchisee_id'],)).fetchall()
            data['payments'] = cur.execute('SELECT * FROM payment_transactions WHERE franchisee_id=? ORDER BY id DESC LIMIT 30', (user['franchisee_id'],)).fetchall()
            data['banks'] = cur.execute('SELECT * FROM franchise_bank_accounts WHERE franchisee_id=?', (user['franchisee_id'],)).fetchall()
        elif user['role'] == 'GUARD':
            data['readings'] = cur.execute('SELECT * FROM device_readings WHERE outlet_id=? ORDER BY id DESC LIMIT 50', (user['outlet_id'],)).fetchall()
            data['incoming'] = cur.execute("SELECT 'BILL' kind,b.id,b.bill_number number FROM bills b WHERE b.outlet_id=? AND b.status='HANDED_TO_GUARD' AND b.id NOT IN (SELECT bill_id FROM docket_bills) UNION ALL SELECT 'DOCKET',d.id,d.docket_number FROM dockets d WHERE d.outlet_id=? AND d.status='HANDED_TO_GUARD'", (user['outlet_id'],user['outlet_id'])).fetchall()
        elif user['role'] == 'DELIVERY':
            data['tasks'] = cur.execute("SELECT * FROM delivery_tasks WHERE assigned_user_id=? AND status!='DELIVERED' ORDER BY id DESC", (user['id'],)).fetchall()
    return a.templates.TemplateResponse('workspace.html', data)


def claim_order(request: Request, order_id: int):
    user = require_user(request, {'POS'})
    with a.get_conn() as conn:
        cur = conn.cursor()
        order = cur.execute("SELECT * FROM orders WHERE id=? AND outlet_id=? AND status='EXECUTED' AND id NOT IN (SELECT order_id FROM bills)", (order_id,user['outlet_id'])).fetchone()
        if not order:
            return RedirectResponse('/pos/home?error=Order+is+no+longer+available#queue', status_code=303)
        lease=cur.execute('SELECT * FROM pick_sessions WHERE order_id=?',(order_id,)).fetchone()
        if lease and lease['user_id'] != user['id'] and lease['expires_at'] > time.time():
            return RedirectResponse('/pos/home?error=Another+POS+operator+is+picking+this+order#queue', status_code=303)
        cur.execute('INSERT INTO pick_sessions VALUES(?,?,?) ON CONFLICT(order_id) DO UPDATE SET user_id=excluded.user_id,expires_at=excluded.expires_at',(order_id,user['id'],time.time()+900))
        a.audit(cur,user,'CLAIM','ORDER',order_id)
    return redirect('Order claimed for 15 minutes. Each scan renews the claim.', '/pos/home#queue')


def scan_item(request: Request, order_id: int, sku: str = Form(...), qty: int = Form(1), item_id: int | None = Form(None)):
    user = require_user(request, {'POS'})
    with a.get_conn() as conn:
        cur=conn.cursor()
        lease=cur.execute("SELECT p.* FROM pick_sessions p JOIN orders o ON o.id=p.order_id WHERE p.order_id=? AND o.outlet_id=? AND o.status='EXECUTED' AND o.id NOT IN (SELECT order_id FROM bills)",(order_id,user['outlet_id'])).fetchone()
        if not lease or lease['user_id'] != user['id'] or lease['expires_at'] < time.time():
            return RedirectResponse('/pos/home?error=Claim+this+order+before+scanning#queue', status_code=303)
        rows=cur.execute('SELECT oi.*,COALESCE(pi.qty,0) picked FROM order_items oi JOIN products p ON p.id=oi.product_id LEFT JOIN picked_items pi ON pi.order_item_id=oi.id WHERE oi.order_id=? AND p.sku_code=?',(order_id,sku.strip())).fetchall()
        row=next((r for r in rows if r['id']==item_id),None) if item_id else next((r for r in rows if r['picked']<r['qty']),None)
        if not row or qty < 1 or row['picked']+qty>row['qty']:
            return RedirectResponse('/pos/home?error=SKU+not+ordered+or+quantity+exceeds+the+order#queue', status_code=303)
        cur.execute('INSERT INTO picked_items VALUES(?,?,?,?) ON CONFLICT(order_item_id) DO UPDATE SET qty=excluded.qty,user_id=excluded.user_id,updated_at=excluded.updated_at',(row['id'],row['picked']+qty,user['id'],a.now_str()))
        cur.execute('UPDATE pick_sessions SET expires_at=? WHERE order_id=?',(time.time()+900,order_id))
        a.audit(cur,user,'SCAN','ORDER',order_id,f'{sku}: +{qty}')
    return redirect('Item picked', '/pos/home#queue')


def dispatch_label(request: Request, kind: str, entity_id: int):
    user=require_user(request, {'ADMIN','POS','GUARD'})
    with a.get_conn() as conn:
        row=entity(conn.cursor(),kind,entity_id)
        if user['role']!='ADMIN' and row['outlet_id']!=user['outlet_id']:
            raise HTTPException(403,'Wrong outlet')
        ids=a.order_ids_for_entity(conn.cursor(),kind,entity_id)
        bills=conn.execute(f"SELECT b.bill_number,o.order_number,f.shop_name,f.name franchise_name,f.mobile franchise_phone,COALESCE(NULLIF(f.delivery_address,''),f.address) address FROM bills b JOIN orders o ON o.id=b.order_id JOIN franchisees f ON f.id=o.franchisee_id WHERE o.id IN ({','.join('?' for _ in ids)})",ids).fetchall()
    from reportlab.graphics.barcode import code128
    from reportlab.pdfgen import canvas
    buffer=io.BytesIO()
    pdf=canvas.Canvas(buffer,pagesize=(420,595))
    number=row['bill_number' if kind=='BILL' else 'docket_number']
    pdf.setFont('Helvetica-Bold',18); pdf.drawString(24,560,'Aapoorti Dispatch')
    pdf.setFont('Helvetica',12); pdf.drawString(24,530,number)
    code128.Code128(number,barHeight=45,barWidth=0.7).drawOn(pdf,15,465)
    y=435
    for bill in bills:
        for text in (bill['bill_number']+' / '+bill['order_number'], bill['shop_name'], bill['franchise_name']+' | Contact: '+(bill['franchise_phone'] or 'Not provided'), bill['address'] or ''):
            if y<50: pdf.showPage();pdf.setFont('Helvetica',12);y=550
            pdf.drawString(24,y,str(text)[:60]);y-=18
        y-=12
    pdf.save()
    return Response(buffer.getvalue(),media_type='application/pdf',headers={'Content-Disposition':f'inline; filename="{number}.pdf"'})


def issue_code(request: Request, purpose: str = Form(...), channel: str = Form('APP')):
    user=require_user(request, {'FRANCHISEE'})
    with a.get_conn() as conn:
        cur=conn.cursor()
        try:
            parts=purpose.split(':')
            if parts[0]=='DELIVERY' and len(parts)==2:
                target=cur.execute("SELECT * FROM delivery_tasks WHERE id=? AND franchisee_id=? AND status='OUT_FOR_DELIVERY'",(int(parts[1]),user['franchisee_id'])).fetchone()
            elif parts[0]=='PICKUP' and len(parts)==3:
                target=entity(cur,parts[1],int(parts[2]))
                if target['status'] not in {'WEIGHT_CHECK_PASSED','WEIGHT_CHECK_ALERT'} or target['franchisee_id']!=user['franchisee_id']:
                    target=None
            else: target=None
        except ValueError:
            target=None
        if not target:
            raise HTTPException(400,'This dispatch is not ready for your confirmation')
        latest=cur.execute('SELECT created_at FROM otp_challenges WHERE franchisee_id=? AND purpose=? ORDER BY id DESC LIMIT 1',(user['franchisee_id'],purpose)).fetchone()
        if latest and latest['created_at'] > (datetime.now(a.IST)-timedelta(seconds=30)).strftime('%Y-%m-%d %H:%M:%S'):
            raise HTTPException(429,'Wait 30 seconds before generating another code')
        code=f'{secrets.randbelow(1000000):06d}'
        phone=cur.execute('SELECT mobile FROM franchisees WHERE id=?',(user['franchisee_id'],)).fetchone()[0]
        if channel=='SMS':
            try: providers.send_otp('+91'+phone[-10:],code)
            except providers.ProviderError as error: raise HTTPException(503,str(error))
        elif channel!='APP': raise HTTPException(400,'Invalid code channel')
        cur.execute("UPDATE otp_challenges SET status='REPLACED' WHERE franchisee_id=? AND purpose=? AND status='PENDING'",(user['franchisee_id'],purpose))
        expiry=(datetime.now(a.IST)+timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
        cur.execute('INSERT INTO otp_challenges(user_id,franchisee_id,phone_e164,purpose,provider,otp_hash,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)',(user['id'],user['franchisee_id'],phone,purpose,channel,otp_digest(code),expiry,a.now_str()))
    if 'application/json' in request.headers.get('accept', ''):
        return JSONResponse({'code': code if channel == 'APP' else None, 'purpose': purpose, 'expiry': expiry}, headers={'Cache-Control': 'no-store'})
    return a.templates.TemplateResponse('handover_code.html',{'request':request,'user':user,'code':code if channel=='APP' else None,'purpose':purpose,'expiry':expiry})


def target_program(request: Request, name: str = Form(...), scope: str = Form('TOTAL_SALE'), department: str = Form(''), product_id: int | None = Form(None), starts_on: str = Form(''), ends_on: str = Form(''), slabs: str = Form(...)):
    user=require_user(request,{'ADMIN'})
    if scope not in {'TOTAL_SALE','DEPARTMENT','PRODUCT'} or (scope=='DEPARTMENT' and not department.strip()) or (scope=='PRODUCT' and not product_id):
        raise HTTPException(400,'Choose a valid target scope')
    validate_dates(starts_on,ends_on)
    try:
        pairs=[tuple(map(float,line.split(':'))) for line in slabs.strip().splitlines()]
        if not pairs or any(len(pair)!=2 or not math.isfinite(pair[0]) or not math.isfinite(pair[1]) or pair[0]<=0 or not 0<=pair[1]<=100 for pair in pairs) or len({p[0] for p in pairs})!=len(pairs):
            raise ValueError()
    except (ValueError,TypeError):
        raise HTTPException(400,'Enter one threshold:percent per line, e.g. 50000:2')
    with a.get_conn() as conn:
        cur=conn.cursor()
        if scope=='PRODUCT' and not cur.execute('SELECT 1 FROM products WHERE id=?',(product_id,)).fetchone():
            raise HTTPException(400,'Product not found')
        cur.execute('INSERT INTO target_programs(code,name,scope,department,product_id,starts_on,ends_on,created_at) VALUES(?,?,?,?,?,?,?,?)',('TARGET-'+uuid4().hex[:12],name.strip(),scope,department.strip(),product_id,starts_on,ends_on,a.now_str()))
        pid=cur.lastrowid
        cur.executemany('INSERT INTO target_slabs(program_id,threshold_amount,reward_percent,created_at) VALUES(?,?,?,?)',[(pid,threshold,percent,a.now_str()) for threshold,percent in pairs])
        a.audit(cur,user,'CREATE','TARGET_PROGRAM',pid,json.dumps(pairs))
    return redirect('Target program saved. Rewards accrue on delivered eligible sales.')


def target_status(request: Request, program_id: int, status: str = Form(...)):
    user=require_user(request,{'ADMIN'})
    if status not in {'ACTIVE','INACTIVE'}: raise HTTPException(400,'Invalid status')
    with a.get_conn() as conn:
        conn.execute('UPDATE target_programs SET status=? WHERE id=?',(status,program_id))
        a.audit(conn.cursor(),user,'STATUS','TARGET_PROGRAM',program_id,status)
    return redirect('Target status updated')


def validate_dates(start,end):
    try:
        if start: datetime.strptime(start,'%Y-%m-%d')
        if end: datetime.strptime(end,'%Y-%m-%d')
        if start and end and end<start: raise ValueError()
    except ValueError: raise HTTPException(400,'Invalid effective date range')


def operational_settings(request: Request, require_picking: int = Form(1), geofence_radius_m: float = Form(0), max_bills_per_docket: int = Form(30), weight_tolerance_percent: float = Form(0), wallet_credit_percent: float = Form(100), customer_return_window_days: int = Form(4)):
    user=require_user(request,{'ADMIN'})
    if require_picking not in (0,1) or not 1<=max_bills_per_docket<=100 or not 1<=customer_return_window_days<=30 or not math.isfinite(wallet_credit_percent) or not 0<=wallet_credit_percent<=100 or not math.isfinite(weight_tolerance_percent) or not 0<=weight_tolerance_percent<=100 or not math.isfinite(geofence_radius_m) or not 0<=geofence_radius_m<=10000:
        raise HTTPException(400,'Invalid operational settings')
    with a.get_conn() as conn:
        cur=conn.cursor()
        for key,value in {'require_picking':require_picking,'geofence_radius_m':geofence_radius_m,'max_bills_per_docket':max_bills_per_docket,'weight_tolerance_percent':weight_tolerance_percent,'wallet_credit_percent':100,'customer_return_window_days':customer_return_window_days}.items():
            a.upsert_setting(cur,key,str(value))
        a.audit(cur,user,'CONFIGURE','OPERATIONS',None)
    return redirect('Operational settings saved with change history')


def coordinates(request: Request, kind: str = Form(...), record_id: int = Form(...), latitude: float = Form(...), longitude: float = Form(...), state_code: str = Form('23')):
    user=require_user(request,{'ADMIN'})
    if kind not in {'OUTLET','FRANCHISE'} or not math.isfinite(latitude) or not math.isfinite(longitude) or not -90<=latitude<=90 or not -180<=longitude<=180 or not state_code.isdigit() or len(state_code)!=2:
        raise HTTPException(400,'Invalid coordinates or state code')
    table='outlets' if kind=='OUTLET' else 'franchisees'
    with a.get_conn() as conn:
        result=conn.execute(f'UPDATE {table} SET latitude=?,longitude=? WHERE id=?',(latitude,longitude,record_id))
        if not result.rowcount: raise HTTPException(404,'Location not found')
        if kind=='FRANCHISE': conn.execute('UPDATE franchisees SET state_code=?,route_distance_km=NULL WHERE id=?',(state_code,record_id))
        else: conn.execute('UPDATE franchisees SET route_distance_km=NULL WHERE outlet_id=?',(record_id,))
        a.audit(conn.cursor(),user,'COORDINATES',kind,record_id)
    return redirect('Coordinates saved. Geofence uses straight-line distance.')


def update_route(request: Request, franchisee_id: int):
    user=require_user(request,{'ADMIN'})
    with a.get_conn() as conn:
        franchise=conn.execute('SELECT * FROM franchisees WHERE id=?',(franchisee_id,)).fetchone()
        if not franchise: raise HTTPException(404,'Franchise not found')
        outlet=conn.execute('SELECT * FROM outlets WHERE id=?',(franchise['outlet_id'],)).fetchone()
        if not outlet: raise HTTPException(400,'Outlet not configured')
    try: distance=providers.route_distance(outlet,franchise)
    except providers.ProviderError as error: raise HTTPException(503,str(error))
    with a.get_conn() as conn:
        current=conn.execute('SELECT * FROM franchisees WHERE id=?',(franchisee_id,)).fetchone()
        current_outlet=conn.execute('SELECT * FROM outlets WHERE id=?',(current['outlet_id'],)).fetchone()
        if any(current[k]!=franchise[k] for k in ('latitude','longitude','outlet_id')) or any(current_outlet[k]!=outlet[k] for k in ('latitude','longitude')):
            raise HTTPException(409,'Coordinates changed during routing. Calculate again.')
        conn.execute('UPDATE franchisees SET route_distance_km=?,route_outlet_id=? WHERE id=?',(distance,outlet['id'],franchisee_id))
        a.audit(conn.cursor(),user,'DRIVING_DISTANCE','FRANCHISE',franchisee_id,f'{distance} km')
    return redirect(f'Driving distance saved: {distance} km. New orders use this distance.')


def staff_status(request: Request, user_id: int, status: str = Form(...)):
    user=require_user(request,{'ADMIN'})
    if status not in {'ACTIVE','INACTIVE'} or user_id==user['id']: raise HTTPException(400,'Cannot deactivate your own account')
    with a.get_conn() as conn:
        conn.execute('UPDATE users SET status=?,auth_version=auth_version+1 WHERE id=?',(status,user_id))
        a.audit(conn.cursor(),user,'STATUS','USER',user_id,status)
    return redirect('Staff access updated; previous sessions invalidated')


def staff_access_page(request: Request):
    user=require_user(request,{'ADMIN'})
    with a.get_conn() as conn:
        staff=conn.execute("SELECT id,name,role,status,outlet_id,phone FROM users WHERE role IN ('POS','GUARD','DELIVERY') ORDER BY role,name,id").fetchall()
    return a.templates.TemplateResponse('staff_access.html', {'request':request,'user':user,'staff':staff,'message':request.query_params.get('message','')})


def staff_password_reset(request: Request, user_id: int, new_password: str = Form(..., min_length=12, max_length=128)):
    user=require_user(request,{'ADMIN'})
    with a.get_conn() as conn:
        target=conn.execute("SELECT id,role FROM users WHERE id=?",(user_id,)).fetchone()
        if not target or target['role'] not in {'POS','GUARD','DELIVERY'}:
            raise HTTPException(404,'Resettable staff account not found')
        conn.execute('UPDATE users SET pin=?,auth_version=auth_version+1 WHERE id=?',(hash_password(new_password),user_id))
        a.audit(conn.cursor(),user,'PASSWORD_RESET','USER',user_id,target['role'])
    return redirect('Staff password reset; previous sessions invalidated')


def settlement_create(request: Request, outlet_id: int = Form(...)):
    user=require_user(request,{'ADMIN'})
    with a.get_conn() as conn:
        cur=conn.cursor()
        orders=cur.execute("SELECT id,payable_to_outlet FROM orders WHERE outlet_id=? AND status='DELIVERED' AND id NOT IN (SELECT order_id FROM settlement_orders)",(outlet_id,)).fetchall()
        if not orders: return redirect('No unsettled delivered orders for this outlet')
        total=round(sum(r['payable_to_outlet'] for r in orders),2)
        cur.execute('INSERT INTO settlements(outlet_id,total,created_by,created_at) VALUES(?,?,?,?)',(outlet_id,total,user['id'],a.now_str()))
        sid=cur.lastrowid
        cur.executemany('INSERT INTO settlement_orders VALUES(?,?,?)',[(sid,r['id'],r['payable_to_outlet']) for r in orders])
        a.audit(cur,user,'CREATE','SETTLEMENT',sid,f'{len(orders)} orders; {total}')
    return redirect('Settlement batch created. Review the statement before recording payment.')


def settlement_paid(request: Request, settlement_id: int, reference: str = Form(...,min_length=4), amount: float = Form(...)):
    user=require_user(request,{'ADMIN'})
    positive(amount,allow_zero=True)
    with a.get_conn() as conn:
        cur=conn.cursor()
        row=cur.execute('SELECT * FROM settlements WHERE id=?',(settlement_id,)).fetchone()
        if not row or row['status']!='PENDING' or round(amount,2)!=round(row['total'],2): raise HTTPException(409,'Amount must match a pending settlement')
        cur.execute("UPDATE settlements SET status='PAID',payment_reference=?,paid_at=? WHERE id=?",(reference.strip(),a.now_str(),settlement_id))
        a.audit(cur,user,'RECORD_PAYMENT','SETTLEMENT',settlement_id,reference.strip())
    return redirect('External outlet payment recorded. No bank transfer was initiated by this application.')


def settlement_csv(request: Request, settlement_id: int):
    require_user(request,{'ADMIN'})
    import csv
    with a.get_conn() as conn:
        rows=conn.execute('SELECT o.order_number,o.item_subtotal,o.commission_total,s.amount FROM settlement_orders s JOIN orders o ON o.id=s.order_id WHERE settlement_id=?',(settlement_id,)).fetchall()
    buffer=io.StringIO();writer=csv.writer(buffer)
    writer.writerow(['Order','Merchandise','Commission','Outlet payable'])
    writer.writerows([list(r) for r in rows])
    return Response(buffer.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="settlement-{settlement_id}.csv"'})


def bank_add(request: Request, holder: str = Form(...), bank_name: str = Form(...), account_number: str = Form(...), ifsc: str = Form(...)):
    user=require_user(request,{'FRANCHISEE'})
    import re
    if not re.fullmatch(r'\d{8,20}',account_number) or not re.fullmatch(r'[A-Z]{4}0[A-Z0-9]{6}',ifsc.upper()): raise HTTPException(400,'Invalid bank account or IFSC')
    with a.get_conn() as conn:
        cur=conn.cursor()
        cur.execute("INSERT INTO franchise_bank_accounts(franchisee_id,provider,account_holder_name,bank_name,account_last4,ifsc_code,created_at,updated_at) VALUES(?,'MANUAL',?,?,?,?,?,?)",(user['franchisee_id'],holder.strip(),bank_name.strip(),account_number[-4:],ifsc.upper(),a.now_str(),a.now_str()))
        a.audit(cur,user,'BANK_DETAILS','FRANCHISE',user['franchisee_id'],'Submitted masked account for manual verification')
    return redirect('Bank profile submitted. Admin verifies bank documents outside the app; only the last four digits are stored.')


def bank_verify(request: Request, bank_id: int, reference: str = Form(...,min_length=4)):
    user=require_user(request,{'ADMIN'})
    with a.get_conn() as conn:
        conn.execute("UPDATE franchise_bank_accounts SET verification_status='MANUALLY_VERIFIED',status='ACTIVE',updated_at=? WHERE id=?",(a.now_str(),bank_id))
        a.audit(conn.cursor(),user,'MANUAL_BANK_VERIFICATION','BANK_ACCOUNT',bank_id,reference)
    return redirect('Manual bank verification recorded')


def topup_create(request: Request, amount: float = Form(...), request_key: str = Form(...,min_length=16,max_length=64)):
    user=require_user(request,{'FRANCHISEE'})
    value=positive(amount)
    if value<1 or value>1000000: raise HTTPException(400,'Top-up must be between INR 1 and 10,00,000')
    if not providers.payment_ready(): raise HTTPException(503,'Payment gateway is not configured. Admin can record a verified manual advance.')
    with a.get_conn() as conn:
        cur=conn.cursor()
        old=cur.execute('SELECT * FROM payment_transactions WHERE idempotency_key=?',(request_key,)).fetchone()
        if old:
            if old['franchisee_id']!=user['franchisee_id'] or old['amount_paise']!=round(value*100): raise HTTPException(409,'Payment request already used')
            return RedirectResponse(f'/franchise/payments/{old["id"]}',status_code=303)
        cur.execute("INSERT INTO payment_transactions(franchisee_id,transaction_type,amount_paise,provider,idempotency_key,status,initiated_by,created_at,updated_at) VALUES(?,'TOPUP',?,'RAZORPAY',?,'CREATING',?,?,?)",(user['franchisee_id'],round(value*100),request_key,user['id'],a.now_str(),a.now_str()))
        tid=cur.lastrowid
    # Commit the local intent before talking to the provider. An ambiguous timeout is never retried automatically.
    try:
        remote=providers.razorpay('orders',{'amount':round(value*100),'currency':'INR','receipt':f'EF-{tid}','notes':{'transaction_id':str(tid)}})
        if not remote.get('id') or remote.get('amount')!=round(value*100) or remote.get('currency')!='INR': raise providers.ProviderError('Unexpected provider order response')
    except providers.ProviderError:
        with a.get_conn() as conn:
            conn.execute("UPDATE payment_transactions SET status='REVIEW',failure_message='Order creation uncertain; reconcile provider receipt before retrying',updated_at=? WHERE id=?",(a.now_str(),tid))
        return redirect('Payment creation needs reconciliation. No wallet credit was applied.')
    with a.get_conn() as conn:
        conn.execute("UPDATE payment_transactions SET status='PENDING',provider_reference=?,updated_at=? WHERE id=?",(remote['id'],a.now_str(),tid))
    return RedirectResponse(f'/franchise/payments/{tid}',status_code=303)


def topup_page(request: Request, transaction_id: int):
    user=require_user(request,{'FRANCHISEE'})
    with a.get_conn() as conn:
        row=conn.execute('SELECT * FROM payment_transactions WHERE id=? AND franchisee_id=?',(transaction_id,user['franchisee_id'])).fetchone()
    if not row: raise HTTPException(404,'Payment not found')
    if 'application/json' in request.headers.get('accept', ''):
        with a.get_conn() as conn:
            balance = conn.execute('SELECT wallet_balance FROM franchisees WHERE id=?', (user['franchisee_id'],)).fetchone()['wallet_balance']
        return JSONResponse({'id': row['id'], 'status': row['status'], 'amount': row['amount_paise'],
                             'order_id': row['provider_reference'], 'key': os.getenv('RAZORPAY_KEY_ID', ''),
                             'wallet_balance': balance, 'message': row['failure_message'] or ''}, headers={'Cache-Control': 'no-store'})
    return a.templates.TemplateResponse('payment.html',{'request':request,'user':user,'payment':row,'key_id':os.getenv('RAZORPAY_KEY_ID','')})


def credit_payment(cur,payment):
    row=cur.execute("SELECT * FROM payment_transactions WHERE provider='RAZORPAY' AND provider_reference=?",(payment.get('order_id'),)).fetchone()
    if not row: raise HTTPException(409,'Unknown payment order; reconcile the provider receipt')
    if payment.get('status')!='captured' or payment.get('amount')!=row['amount_paise'] or payment.get('currency')!=row['currency'] or not payment.get('id'):
        raise HTTPException(400,'Payment amount, currency, or capture status does not match')
    if row['status']=='CREDITED':
        if row['payment_id']!=payment['id']: raise HTTPException(409,'Order already credited with another payment')
        return
    a.wallet_entry(cur,row['franchisee_id'],'TOPUP',row['amount_paise']/100,'PAYMENT',row['id'],'Verified Razorpay captured payment',None)
    ledger=cur.lastrowid
    cur.execute("UPDATE payment_transactions SET status='CREDITED',payment_id=?,wallet_ledger_id=?,verified_at=?,updated_at=?,failure_message='' WHERE id=?",(payment['id'],ledger,a.now_str(),a.now_str(),row['id']))
    a.audit(cur,None,'PAYMENT_CREDIT','PAYMENT',row['id'],payment['id'])


async def payment_webhook(request: Request):
    raw=await request.body()
    secret=os.getenv('RAZORPAY_WEBHOOK_SECRET','')
    signature=request.headers.get('x-razorpay-signature','')
    if not secret or not hmac.compare_digest(hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest(),signature): raise HTTPException(401,'Invalid webhook signature')
    try:
        payload=json.loads(raw)
        event=payload['event']
        payment=payload.get('payload',{}).get('payment',{}).get('entity',{})
    except (ValueError,KeyError,AttributeError): raise HTTPException(400,'Invalid webhook payload')
    event_id=request.headers.get('x-razorpay-event-id') or hashlib.sha256(raw).hexdigest()
    with a.get_conn() as conn:
        cur=conn.cursor()
        old=cur.execute("SELECT * FROM payment_webhook_events WHERE provider='RAZORPAY' AND provider_event_id=?",(event_id,)).fetchone()
        if old:
            if old['payload_hash']!=hashlib.sha256(raw).hexdigest(): raise HTTPException(409,'Event ID payload conflict')
            return {'received':True,'duplicate':True}
        if event=='payment.captured': credit_payment(cur,payment)
        elif event=='payment.failed':
            cur.execute("UPDATE payment_transactions SET failure_message='Payment attempt failed. Retry checkout or reconcile.',updated_at=? WHERE provider_reference=? AND status!='CREDITED'",(a.now_str(),payment.get('order_id','')))
        cur.execute("INSERT INTO payment_webhook_events(provider,provider_event_id,event_type,payload_hash,processing_status,received_at,processed_at) VALUES('RAZORPAY',?,?,?,'PROCESSED',?,?)",(event_id,event,hashlib.sha256(raw).hexdigest(),a.now_str(),a.now_str()))
    return {'received':True}


def reconcile_payment(request: Request, transaction_id: int, provider_order_id: str = Form('')):
    user=require_user(request,{'ADMIN'})
    with a.get_conn() as conn:
        row=conn.execute('SELECT * FROM payment_transactions WHERE id=?',(transaction_id,)).fetchone()
    if not row or row['provider']!='RAZORPAY': raise HTTPException(404,'Gateway transaction not found')
    reference=row['provider_reference'] or provider_order_id.strip()
    if not reference.startswith('order_') or not reference.replace('_','').isalnum(): raise HTTPException(400,'Enter the Razorpay order ID from the receipt')
    try:
        order=providers.razorpay('orders/'+reference)
        if order.get('receipt')!=f'EF-{transaction_id}' or order.get('amount')!=row['amount_paise'] or order.get('currency')!='INR': raise HTTPException(400,'Provider receipt does not match this transaction')
        payments=providers.razorpay('orders/'+reference+'/payments')
    except providers.ProviderError as error: raise HTTPException(503,str(error))
    with a.get_conn() as conn:
        cur=conn.cursor()
        cur.execute('UPDATE payment_transactions SET provider_reference=? WHERE id=?',(reference,transaction_id))
        for payment in payments.get('items',[]):
            if payment.get('status')=='captured': credit_payment(cur,payment)
        a.audit(cur,user,'RECONCILE','PAYMENT',transaction_id,reference)
    return redirect('Provider status reconciled; only captured matching payments credit the wallet')


def owned_task(cur,task_id,user):
    task=cur.execute('SELECT t.* FROM delivery_tasks t JOIN franchisees f ON f.id=t.franchisee_id WHERE t.id=? AND t.assigned_user_id=? AND f.outlet_id=?',(task_id,user['id'],user['outlet_id'])).fetchone()
    if not task: raise HTTPException(404,'Task not assigned to you')
    return task


async def proof_upload(request: Request, task_id: int, kind: str = Form(...), proof: UploadFile = File(...)):
    user=require_user(request,{'DELIVERY'})
    content=await proof.read(5*1024*1024+1)
    if kind not in {'PHOTO','SIGNATURE'} or len(content)>5*1024*1024 or proof.content_type not in a.PRODUCT_IMAGE_SIGNATURES or not a.PRODUCT_IMAGE_SIGNATURES[proof.content_type][1](content):
        raise HTTPException(400,'Upload a JPEG, PNG or WebP photo/signature up to 5 MB')
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(content)) as uploaded:
            if uploaded.width*uploaded.height>25000000: raise ValueError()
            uploaded.verify()
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):
        raise HTTPException(400,'The uploaded file is not a valid image')
    with a.get_conn() as conn:
        cur=conn.cursor();task=owned_task(cur,task_id,user)
        if task['status']!='OUT_FOR_DELIVERY': raise HTTPException(409,'Proof is accepted only during delivery')
        folder=a.DATA_DIR/'proofs';folder.mkdir(exist_ok=True)
        name=uuid4().hex+a.PRODUCT_IMAGE_SIGNATURES[proof.content_type][0]
        (folder/name).write_bytes(content)
        cur.execute('INSERT INTO proofs(task_id,path,mime,kind,created_by,created_at) VALUES(?,?,?,?,?,?)',(task_id,name,proof.content_type,kind,user['id'],a.now_str()))
        a.audit(cur,user,'UPLOAD_PROOF','DELIVERY_TASK',task_id,kind)
    return redirect('Delivery proof uploaded. The receiver code is still required to complete delivery.', '/delivery/home')


def proof_view(request: Request, proof_id: int):
    user=require_user(request)
    with a.get_conn() as conn:
        row=conn.execute('SELECT p.*,t.assigned_user_id,t.franchisee_id FROM proofs p JOIN delivery_tasks t ON t.id=p.task_id WHERE p.id=?',(proof_id,)).fetchone()
    if not row or not (user['role']=='ADMIN' or (user['role']=='FRANCHISEE' and user['franchisee_id']==row['franchisee_id']) or (user['role']=='DELIVERY' and user['id']==row['assigned_user_id'])): raise HTTPException(404,'Proof not found')
    return FileResponse(a.DATA_DIR/'proofs'/row['path'],media_type=row['mime'])


def task_location(request: Request, task_id: int, latitude: float = Form(...), longitude: float = Form(...)):
    user=require_user(request,{'DELIVERY'})
    if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90<=latitude<=90 or not -180<=longitude<=180: raise HTTPException(400,'Invalid GPS coordinates')
    with a.get_conn() as conn:
        task=owned_task(conn.cursor(),task_id,user)
        if task['status'] not in {'PICKED_UP','OUT_FOR_DELIVERY'}: raise HTTPException(409,'Trip is not active')
        conn.execute('UPDATE delivery_tasks SET latitude=?,longitude=?,location_at=? WHERE id=?',(latitude,longitude,a.now_str(),task_id))
    return {'saved':True}


def franchise_tracking(request: Request):
    user=require_user(request,{'FRANCHISEE'})
    with a.get_conn() as conn:
        rows=conn.execute('''SELECT t.id task_id,t.status,t.latitude,t.longitude,t.location_at,
                                    t.entity_type,t.entity_id,
                                    CASE WHEN t.entity_type='BILL' THEN b.bill_number ELSE d.docket_number END dispatch_number,
                                    f.latitude destination_latitude,f.longitude destination_longitude
                             FROM delivery_tasks t
                             LEFT JOIN bills b ON t.entity_type='BILL' AND b.id=t.entity_id
                             LEFT JOIN dockets d ON t.entity_type='DOCKET' AND d.id=t.entity_id
                             JOIN franchisees f ON f.id=t.franchisee_id
                             WHERE t.franchisee_id=? AND t.status IN ('ASSIGNED','ACCEPTED','PICKED_UP','OUT_FOR_DELIVERY')
                             ORDER BY t.id DESC''',(user['franchisee_id'],)).fetchall()
    return {'tasks':[dict(row) for row in rows]}


def franchise_tracking_route(request: Request, task_id: int):
    user=require_user(request,{'FRANCHISEE'})
    with a.get_conn() as conn:
        task=conn.execute('''SELECT t.status,t.latitude,t.longitude,f.latitude destination_latitude,f.longitude destination_longitude
                             FROM delivery_tasks t JOIN franchisees f ON f.id=t.franchisee_id
                             WHERE t.id=? AND t.franchisee_id=?''',(task_id,user['franchisee_id'])).fetchone()
    if not task: raise HTTPException(404,'Delivery trip not found')
    if task['status']!='OUT_FOR_DELIVERY': raise HTTPException(409,'The driver is not currently on the way')
    try:
        return providers.route_details(task['latitude'],task['longitude'],task['destination_latitude'],task['destination_longitude'])
    except providers.ProviderError as error:
        raise HTTPException(503,str(error)) from error


def task_failed(request: Request, task_id: int, reason: str = Form(...,min_length=5)):
    user=require_user(request,{'DELIVERY'})
    with a.get_conn() as conn:
        cur=conn.cursor();task=owned_task(cur,task_id,user)
        if task['status'] not in {'ACCEPTED','PICKED_UP','OUT_FOR_DELIVERY'}: raise HTTPException(409,'Task cannot be marked failed in its current state')
        cur.execute("UPDATE delivery_tasks SET failed_from=status,status='FAILED',failure_reason=? WHERE id=?",(reason.strip(),task_id))
        for oid in a.order_ids_for_entity(cur,task['entity_type'],task['entity_id']): a.add_order_event(cur,oid,'DELIVERY_FAILED',reason.strip(),user['id'])
        a.audit(cur,user,'FAILED','DELIVERY_TASK',task_id,reason.strip())
    return redirect('Failure recorded. Admin can reassign or reopen the delivery.', '/delivery/home')


def task_admin(request: Request, task_id: int, action: str = Form(...), reason: str = Form(...,min_length=10), assigned_user_id: int | None = Form(None)):
    user=require_user(request,{'ADMIN'})
    with a.get_conn() as conn:
        cur=conn.cursor();task=cur.execute('SELECT * FROM delivery_tasks WHERE id=?',(task_id,)).fetchone()
        if not task or task['status']=='DELIVERED': raise HTTPException(409,'Task is already completed or missing')
        dispatch=entity(cur,task['entity_type'],task['entity_id'])
        if action=='REASSIGN':
            driver=cur.execute("SELECT 1 FROM users WHERE id=? AND role='DELIVERY' AND status='ACTIVE' AND outlet_id=?",(assigned_user_id,dispatch['outlet_id'])).fetchone()
            if not driver: raise HTTPException(400,'Select an active driver from the same outlet')
            if task['status']=='FAILED':
                resume=task['failed_from'] if task['failed_from'] in {'ACCEPTED','PICKED_UP','OUT_FOR_DELIVERY'} else 'ASSIGNED'
                cur.execute('UPDATE delivery_tasks SET assigned_user_id=?,status=?,failure_reason=NULL WHERE id=?',(assigned_user_id,resume,task_id))
            else:
                cur.execute('UPDATE delivery_tasks SET assigned_user_id=? WHERE id=?',(assigned_user_id,task_id))
        elif action=='REOPEN':
            if task['status']!='FAILED': raise HTTPException(409,'Only failed deliveries can be reopened')
            resume=task['failed_from'] if task['failed_from'] in {'ACCEPTED','PICKED_UP','OUT_FOR_DELIVERY'} else 'ASSIGNED'
            cur.execute('UPDATE delivery_tasks SET status=?,failure_reason=NULL WHERE id=?',(resume,task_id))
        elif action=='FORCE_CLOSE':
            if task['status'] not in {'OUT_FOR_DELIVERY','FAILED'}: raise HTTPException(409,'Only a dispatched delivery may be force closed')
            if task['status']=='FAILED' and task['failed_from'] not in {'PICKED_UP','OUT_FOR_DELIVERY'}: raise HTTPException(409,'Goods have not left for delivery')
            cur.execute("UPDATE delivery_tasks SET status='DELIVERED',delivered_at=?,pod_type='ADMIN_OVERRIDE',pod_reference=?,remarks=? WHERE id=?",(a.now_str(),f'ADMIN-{user["id"]}',reason,task_id))
            table='bills' if task['entity_type']=='BILL' else 'dockets'
            cur.execute(f"UPDATE {table} SET status='DELIVERED',updated_at=? WHERE id=?",(a.now_str(),task['entity_id']))
            if task['entity_type']=='DOCKET':cur.execute("UPDATE bills SET status='DELIVERED',updated_at=? WHERE id IN(SELECT bill_id FROM docket_bills WHERE docket_id=?)",(a.now_str(),task['entity_id']))
            a.complete_entity_orders(cur,task['entity_type'],task['entity_id'],user['id'])
            cur.execute("INSERT INTO handover_logs(entity_type,entity_id,from_role,to_role,notes,created_at,from_user_id,handover_mode) VALUES(?,?,'ADMIN','FRANCHISEE',?,?,?,'ADMIN_OVERRIDE')",(task['entity_type'],task['entity_id'],reason,a.now_str(),user['id']))
        else: raise HTTPException(400,'Invalid dispatch action')
        for oid in a.order_ids_for_entity(cur,task['entity_type'],task['entity_id']):a.add_order_event(cur,oid,action,reason,user['id'])
        a.audit(cur,user,action,'DELIVERY_TASK',task_id,f'{reason}; assigned={assigned_user_id}')
    return redirect('Delivery control applied and audited')


def guard_receive(request: Request, dispatch_number: str = Form(...)):
    user=require_user(request,{'GUARD'})
    with a.get_conn() as conn:
        cur=conn.cursor();row=None
        for kind,table,column in [('BILL','bills','bill_number'),('DOCKET','dockets','docket_number')]:
            row=cur.execute(f"SELECT * FROM {table} WHERE {column}=? AND outlet_id=? AND status='HANDED_TO_GUARD'",(dispatch_number.strip(),user['outlet_id'])).fetchone()
            if row: break
        if not row: raise HTTPException(404,'Scan a dispatch awaiting receipt at your outlet')
        if kind=='BILL' and cur.execute('SELECT 1 FROM docket_bills WHERE bill_id=?',(row['id'],)).fetchone(): raise HTTPException(409,'Receive the complete docket')
        cur.execute(f"UPDATE {table} SET status='AT_GUARD_DESK',updated_at=? WHERE id=?",(a.now_str(),row['id']))
        if kind=='DOCKET': cur.execute("UPDATE bills SET status='AT_GUARD_DESK',updated_at=? WHERE id IN(SELECT bill_id FROM docket_bills WHERE docket_id=?)",(a.now_str(),row['id']))
        for oid in a.order_ids_for_entity(cur,kind,row['id']): a.add_order_event(cur,oid,'AT_GUARD_DESK','Guard received '+dispatch_number,user['id'])
        cur.execute("INSERT INTO handover_logs(entity_type,entity_id,from_role,to_role,notes,created_at,to_user_id,handover_mode) VALUES(?,?,'POS','GUARD',?,?,?,'SCAN')",(kind,row['id'],dispatch_number,a.now_str(),user['id']))
        a.audit(cur,user,'RECEIVE',kind,row['id'],dispatch_number)
    return redirect('Receipt acknowledged. Continue with weight verification.','/guard/home')


async def device_reading(request: Request):
    raw=await request.body()
    try:
        settings=json.loads(os.getenv('DEVICE_KEYS_JSON','{}'))
        device_id=request.headers.get('x-device-id','')
        config=settings[device_id]
        timestamp=request.headers.get('x-device-timestamp','')
        if abs(time.time()-float(timestamp))>120: raise ValueError()
        expected=hmac.new(config['secret'].encode(),timestamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,request.headers.get('x-device-signature','')): raise ValueError()
    except (KeyError,ValueError,TypeError): raise HTTPException(401,'Invalid device authentication or timestamp')
    try:
        body=json.loads(raw);kind=body['entity_type'];eid=int(body['entity_id']);key=str(body['request_id'])
        weight=float(body['weight_kg']) if body.get('weight_kg') is not None else None
        count=int(body['item_count']) if body.get('item_count') is not None else None
        if not key or len(key)>100 or (weight is None and count is None) or (weight is not None and (not math.isfinite(weight) or weight<0)) or (count is not None and count<0): raise ValueError()
    except (ValueError,KeyError,TypeError): raise HTTPException(400,'Invalid device reading')
    with a.get_conn() as conn:
        cur=conn.cursor();row=entity(cur,kind,eid)
        if row['outlet_id']!=config['outlet_id']: raise HTTPException(403,'Device is registered to a different outlet')
        old=cur.execute('SELECT * FROM device_readings WHERE request_id=?',(device_id+':'+key,)).fetchone()
        if old:
            if old['entity_type']!=kind or old['entity_id']!=eid or old['weight_kg']!=weight or old['item_count']!=count: raise HTTPException(409,'Reading ID conflict')
            return {'reading_id':old['id'],'duplicate':True}
        if row['status'] not in {'AT_GUARD_DESK','WEIGHT_CHECK_PASSED','WEIGHT_CHECK_ALERT'}: raise HTTPException(409,'Dispatch not at guard')
        cur.execute('INSERT INTO device_readings(device_id,request_id,outlet_id,entity_type,entity_id,weight_kg,item_count,created_at) VALUES(?,?,?,?,?,?,?,?)',(device_id,device_id+':'+key,config['outlet_id'],kind,eid,weight,count,a.now_str()))
        return {'reading_id':cur.lastrowid}


def apply_reading(request: Request, reading_id: int):
    user=require_user(request,{'GUARD'})
    with a.get_conn() as conn:
        cur=conn.cursor();reading=cur.execute('SELECT * FROM device_readings WHERE id=? AND outlet_id=?',(reading_id,user['outlet_id'])).fetchone()
        if not reading or reading['applied_at']: raise HTTPException(409,'Reading unavailable or already used')
        kind,eid=reading['entity_type'],reading['entity_id'];row=entity(cur,kind,eid)
        if row['status'] not in {'AT_GUARD_DESK','WEIGHT_CHECK_PASSED','WEIGHT_CHECK_ALERT'}: raise HTTPException(409,'Dispatch no longer at guard')
        ids=a.order_ids_for_entity(cur,kind,eid)
        expected_count=cur.execute(f"SELECT SUM(qty) FROM order_items WHERE order_id IN ({','.join('?' for _ in ids)})",ids).fetchone()[0]
        mismatch=reading['item_count'] is not None and reading['item_count']!=expected_count
        actual=reading['weight_kg'] if reading['weight_kg'] is not None else row['actual_weight_kg']
        if actual is None: raise HTTPException(409,'A weight reading is also required')
        variance=round(actual-row['expected_weight_kg'],3)
        tolerance=max(float(a.get_setting(cur,'weight_tolerance_kg','0.3')),row['expected_weight_kg']*float(a.get_setting(cur,'weight_tolerance_percent','0'))/100)
        check='ALERT' if mismatch or abs(variance)>tolerance else 'PASS'
        status='WEIGHT_CHECK_ALERT' if check=='ALERT' else 'WEIGHT_CHECK_PASSED'
        table='bills' if kind=='BILL' else 'dockets'
        cur.execute(f'UPDATE {table} SET actual_weight_kg=?,weight_variance_kg=?,weight_check_status=?,status=?,updated_at=? WHERE id=?',(actual,variance,check,status,a.now_str(),eid))
        if kind=='DOCKET':cur.execute('UPDATE bills SET status=?,updated_at=? WHERE id IN(SELECT bill_id FROM docket_bills WHERE docket_id=?)',(status,a.now_str(),eid))
        cur.execute('UPDATE device_readings SET applied_at=? WHERE id=?',(a.now_str(),reading_id))
        for oid in ids:a.add_order_event(cur,oid,status,f'Device {reading["device_id"]}; quantity mismatch={mismatch}',user['id'])
        a.audit(cur,user,'APPLY_DEVICE_READING',kind,eid,f'{check}; reading={reading_id}; count mismatch={mismatch}')
    return redirect('Machine reading applied: '+check,'/guard/home')


def event_feed(request: Request, after: int = 0):
    user=require_user(request)
    with a.get_conn() as conn:
        filters='e.id>?';params=[max(0,after)]
        if user['role']=='FRANCHISEE':filters+=' AND o.franchisee_id=?';params.append(user['franchisee_id'])
        elif user['role'] in {'POS','GUARD'}:filters+=' AND o.outlet_id=?';params.append(user['outlet_id'])
        elif user['role']=='DELIVERY':
            filters+=" AND EXISTS(SELECT 1 FROM delivery_tasks t LEFT JOIN bills b ON t.entity_type='BILL' AND b.id=t.entity_id LEFT JOIN docket_bills db ON t.entity_type='DOCKET' AND db.docket_id=t.entity_id LEFT JOIN bills bb ON bb.id=db.bill_id WHERE t.assigned_user_id=? AND (b.order_id=o.id OR bb.order_id=o.id))";params.append(user['id'])
        rows=conn.execute(f'SELECT e.id,e.order_id,e.status,e.created_at,o.order_number FROM order_events e JOIN orders o ON o.id=e.order_id WHERE {filters} ORDER BY e.id DESC LIMIT 30',params).fetchall()
    return {'events':[dict(row) for row in reversed(rows)],'cursor':rows[0]['id'] if rows else after}


ROUTES = [
    ('/admin/inventory/receive','POST',receive_inventory),
    ('/workspace','GET',workspace),('/account','GET',account),('/account/password','POST',password_change),('/admin/staff','GET',staff_access_page),
    ('/pos/orders/{order_id}/claim','POST',claim_order),('/pos/orders/{order_id}/scan','POST',scan_item),
    ('/dispatch/{kind}/{entity_id}/label.pdf','GET',dispatch_label),('/franchise/dispatch-code','POST',issue_code),
    ('/admin/targets','POST',target_program),('/admin/targets/{program_id}/status','POST',target_status),
    ('/admin/operations/settings','POST',operational_settings),('/admin/coordinates','POST',coordinates),('/admin/users/{user_id}/status','POST',staff_status),('/admin/users/{user_id}/password','POST',staff_password_reset),
    ('/admin/franchisees/{franchisee_id}/route','POST',update_route),
    ('/admin/settlements','POST',settlement_create),('/admin/settlements/{settlement_id}/paid','POST',settlement_paid),('/admin/settlements/{settlement_id}.csv','GET',settlement_csv),
    ('/franchise/banks','POST',bank_add),('/admin/banks/{bank_id}/verify','POST',bank_verify),
    ('/franchise/topups','POST',topup_create),('/franchise/payments/{transaction_id}','GET',topup_page),('/integrations/razorpay/webhook','POST',payment_webhook),('/admin/payments/{transaction_id}/reconcile','POST',reconcile_payment),
    ('/delivery/tasks/{task_id}/proof','POST',proof_upload),('/proofs/{proof_id}','GET',proof_view),('/delivery/tasks/{task_id}/location','POST',task_location),('/delivery/tasks/{task_id}/failed','POST',task_failed),('/franchise/tracking','GET',franchise_tracking),('/franchise/tracking/{task_id}/route','GET',franchise_tracking_route),
    ('/admin/delivery/tasks/{task_id}','POST',task_admin),('/guard/receive','POST',guard_receive),('/integrations/device/reading','POST',device_reading),('/guard/readings/{reading_id}/apply','POST',apply_reading),('/api/events','GET',event_feed),
]
