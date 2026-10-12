"""Dispatch policy and seal controls shared by web and mobile callers."""
from fastapi import Form, HTTPException, Request
from app import operations as ops

MODES = {'BOTH': {'FRANCHISE_PICKUP', 'DELIVERY_MAN'}, 'FRANCHISE_PICKUP': {'FRANCHISE_PICKUP'}, 'DELIVERY_MAN': {'DELIVERY_MAN'}}


def migrate(cur):
    cur.execute('CREATE TABLE IF NOT EXISTS revoked_access_tokens(jti TEXT PRIMARY KEY, expires_at INTEGER NOT NULL)')
    for table, additions in {
        'outlets': {'delivery_mode': "TEXT NOT NULL DEFAULT 'BOTH'"},
        'franchisees': {'delivery_mode': "TEXT NOT NULL DEFAULT 'BOTH'"},
        'promotions': {'franchisee_id': 'INTEGER'},
        'bills': {'seal_status': "TEXT NOT NULL DEFAULT 'PENDING'", 'seal_notes': 'TEXT', 'seal_checked_by': 'INTEGER', 'seal_checked_at': 'TEXT'},
        'dockets': {'seal_status': "TEXT NOT NULL DEFAULT 'PENDING'", 'seal_notes': 'TEXT', 'seal_checked_by': 'INTEGER', 'seal_checked_at': 'TEXT'},
    }.items():
        existing = {r['name'] for r in cur.execute(f'PRAGMA table_info({table})')}
        for name, definition in additions.items():
            if name not in existing:
                cur.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    for key, value in {'docket_policy': 'OPTIONAL', 'delivery_pricing_mode': 'PER_KM', 'delivery_fixed_charge': '0', 'require_seal_check': '1'}.items():
        cur.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (key, value))
    cur.execute('PRAGMA user_version=3')


def allowed_modes(cur, franchise):
    outlet = cur.execute('SELECT delivery_mode FROM outlets WHERE id=?', (franchise['outlet_id'],)).fetchone()
    return MODES[franchise['delivery_mode']] & MODES[outlet['delivery_mode'] if outlet else 'BOTH']


def validate_mode(cur, fid, mode):
    franchise = cur.execute('SELECT * FROM franchisees WHERE id=?', (fid,)).fetchone()
    if not franchise or mode not in allowed_modes(cur, franchise):
        raise HTTPException(409, 'This fulfilment mode is disabled for your franchise or outlet')


def make_docket(cur, rows, user):
    a = ops.a
    if not rows or len(rows) > int(a.get_setting(cur, 'max_bills_per_docket', '30')):
        raise HTTPException(400, 'Docket bill limit exceeded')
    if len({(r['franchisee_id'], r['outlet_id'], r['fulfillment_mode']) for r in rows}) != 1:
        raise HTTPException(400, 'Select bills from one franchise, outlet and fulfilment mode')
    number = a.next_code('DCK-', 'dockets', 'docket_number')
    cur.execute("INSERT INTO dockets(docket_number,franchisee_id,outlet_id,status,expected_weight_kg,created_at) VALUES(?,?,?,'DOCKET_CREATED',?,?)",
                (number, rows[0]['franchisee_id'], rows[0]['outlet_id'], sum(r['expected_weight_kg'] or 0 for r in rows), a.now_str()))
    did = cur.lastrowid
    for row in rows:
        cur.execute('INSERT INTO docket_bills(docket_id,bill_id) VALUES(?,?)', (did, row['id']))
        cur.execute("UPDATE bills SET status='DOCKET_CREATED',updated_at=? WHERE id=?", (a.now_str(), row['id']))
        a.add_order_event(cur, row['order_id'], 'DOCKET_CREATED', number, user['id'])
    a.audit(cur, user, 'CREATE', 'DOCKET', did, f'{number}: {len(rows)} bills')
    return did


def prepare_handover(cur, bill, user):
    """AUTO groups the compatible packed selection at handover, never mid-packing."""
    a = ops.a
    policy = a.get_setting(cur, 'docket_policy', 'OPTIONAL')
    if policy == 'REQUIRED':
        raise HTTPException(409, 'Create a docket before handover; docket policy is mandatory')
    if policy != 'AUTO':
        return 'BILL', bill['id']
    rows = cur.execute("""SELECT b.*,o.fulfillment_mode FROM bills b JOIN orders o ON o.id=b.order_id
        WHERE b.franchisee_id=? AND b.outlet_id=? AND b.status='PACKED_INDIVIDUALLY'
        AND o.fulfillment_mode=(SELECT fulfillment_mode FROM orders WHERE id=?)
        AND b.id NOT IN(SELECT bill_id FROM docket_bills) ORDER BY b.id LIMIT ?""",
        (bill['franchisee_id'], bill['outlet_id'], bill['order_id'], int(a.get_setting(cur,'max_bills_per_docket','30')))).fetchall()
    # Include the clicked bill even when older compatible bills exceed the group limit.
    if bill['id'] not in {r['id'] for r in rows}:
        clicked = cur.execute('SELECT b.*,o.fulfillment_mode FROM bills b JOIN orders o ON o.id=b.order_id WHERE b.id=?', (bill['id'],)).fetchone()
        rows = rows[:-1] + [clicked]
    return 'DOCKET', make_docket(cur, rows, user)


def delivery_modes(request: Request, kind: str = Form(...), record_id: int = Form(...), mode: str = Form(...)):
    a = ops.a
    user = ops.require_user(request, {'ADMIN'})
    if kind not in {'FRANCHISE', 'OUTLET'} or mode not in MODES:
        raise HTTPException(400, 'Invalid delivery mode')
    table = 'franchisees' if kind == 'FRANCHISE' else 'outlets'
    with a.get_conn() as conn:
        cur = conn.cursor()
        if not cur.execute(f'SELECT 1 FROM {table} WHERE id=?', (record_id,)).fetchone():
            raise HTTPException(404, 'Record not found')
        if kind == 'FRANCHISE':
            other = cur.execute('SELECT o.delivery_mode FROM franchisees f JOIN outlets o ON o.id=f.outlet_id WHERE f.id=?', (record_id,)).fetchone()
            compatible = other and MODES[mode] & MODES[other[0]]
        else:
            compatible = all(MODES[mode] & MODES[r[0]] for r in cur.execute('SELECT delivery_mode FROM franchisees WHERE outlet_id=?', (record_id,)))
        if not compatible:
            raise HTTPException(409, 'The franchise and outlet must share at least one fulfilment mode')
        cur.execute(f'UPDATE {table} SET delivery_mode=? WHERE id=?', (mode, record_id))
        a.audit(cur, user, 'DELIVERY_MODE', kind, record_id, mode)
    return ops.redirect('Fulfilment availability saved')


def policy(request: Request, docket_policy: str = Form(...), require_seal_check: int = Form(1)):
    a = ops.a
    user = ops.require_user(request, {'ADMIN'})
    if docket_policy not in {'OPTIONAL', 'REQUIRED', 'AUTO'} or require_seal_check not in (0, 1):
        raise HTTPException(400, 'Invalid dispatch policy')
    with a.get_conn() as conn:
        cur = conn.cursor()
        for key, value in {'docket_policy': docket_policy, 'require_seal_check': str(require_seal_check)}.items():
            a.upsert_setting(cur, key, value)
        a.audit(cur, user, 'DISPATCH_POLICY', 'SETTINGS', None, docket_policy)
    return ops.redirect('Dispatch policy saved')


def seal_check(request: Request, entity_type: str = Form(...), entity_id: int = Form(...), seal_status: str = Form(...), notes: str = Form('')):
    a = ops.a
    user = ops.require_user(request, {'GUARD'})
    if seal_status not in {'INTACT', 'BROKEN'} or len(notes) > 1000 or (seal_status == 'BROKEN' and len(notes.strip()) < 5):
        raise HTTPException(400, 'Record an intact seal or describe the broken seal')
    with a.get_conn() as conn:
        cur = conn.cursor()
        row = ops.entity(cur, entity_type, entity_id)
        if row['outlet_id'] != user['outlet_id']:
            raise HTTPException(403, 'Dispatch belongs to another outlet')
        if row['status'] not in {'AT_GUARD_DESK', 'WEIGHT_CHECK_PASSED', 'WEIGHT_CHECK_ALERT'}:
            raise HTTPException(409, 'Receive the dispatch before checking its seal')
        if entity_type == 'BILL' and cur.execute('SELECT 1 FROM docket_bills WHERE bill_id=?', (entity_id,)).fetchone():
            raise HTTPException(409, 'Check the complete docket')
        table = 'bills' if entity_type == 'BILL' else 'dockets'
        cur.execute(f'UPDATE {table} SET seal_status=?,seal_notes=?,seal_checked_by=?,seal_checked_at=? WHERE id=?', (seal_status, notes.strip(), user['id'], a.now_str(), entity_id))
        if entity_type == 'DOCKET':
            cur.execute(
                "UPDATE bills SET seal_status=?,seal_notes=?,seal_checked_by=?,seal_checked_at=? WHERE id IN (SELECT bill_id FROM docket_bills WHERE docket_id=?)",
                (seal_status, notes.strip(), user['id'], a.now_str(), entity_id),
            )
        a.audit(cur, user, 'SEAL_CHECK', entity_type, entity_id, f'{seal_status}: {notes.strip()}')
        for oid in a.order_ids_for_entity(cur, entity_type, entity_id):
            a.add_order_event(cur, oid, 'SEAL_CHECK', seal_status, user['id'])
    return ops.redirect('Seal inspection recorded', '/guard/home')


def register(main):
    for path, handler in [('/admin/delivery-modes', delivery_modes), ('/admin/dispatch-policy', policy), ('/guard/seal-check', seal_check)]:
        main.app.add_api_route(path, handler, methods=['POST'])
