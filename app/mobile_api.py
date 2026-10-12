"""Revocable role-scoped mobile credentials and shared operation adapters."""
import asyncio
import inspect
import json
import secrets
import time

import jwt
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import operations as ops
from app.security import session_secret, verify_password

ISSUER = 'aapoorti-e-franchise'
AUDIENCE = 'aapoorti-mobile'
TOKEN_SECONDS = 3600


def token_user(request):
    header = request.headers.get('authorization', '')
    if not header:
        return None
    if not header.startswith('Bearer '):
        raise HTTPException(401, 'Use a bearer access token')
    a = ops.a
    try:
        claims = jwt.decode(header[7:], session_secret(a.DATA_DIR), algorithms=['HS256'], issuer=ISSUER, audience=AUDIENCE,
                            options={'require': ['exp', 'iat', 'sub', 'iss', 'aud', 'role', 'version', 'jti']})
        with a.get_conn() as conn:
            row = conn.execute('SELECT * FROM users WHERE id=? AND status=?', (int(claims['sub']), 'ACTIVE')).fetchone()
            revoked = conn.execute('SELECT 1 FROM revoked_access_tokens WHERE jti=?', (claims['jti'],)).fetchone()
            if not row or revoked or row['auth_version'] != claims['version'] or row['role'] != claims['role']:
                raise ValueError('Revoked token')
            if row['role'] == 'FRANCHISEE' and not conn.execute("SELECT 1 FROM franchisees WHERE id=? AND status='ACTIVE'", (row['franchisee_id'],)).fetchone():
                raise ValueError('Inactive franchise')
        request.state.access_claims = claims
        return dict(row)
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
        raise HTTPException(401, 'Access token is expired or invalid', headers={'WWW-Authenticate': 'Bearer'})


class Credentials(BaseModel):
    login: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


def login(request: Request, credentials: Credentials):
    a = ops.a
    ip = request.client.host if request.client else ''
    key = credentials.login.strip().lower()
    now = time.time()
    with a.get_conn() as conn:
        if conn.execute('SELECT COUNT(*) FROM login_attempts WHERE (login=? OR ip=?) AND created_at>?', (key, ip, now-900)).fetchone()[0] >= 10:
            raise HTTPException(429, 'Too many login attempts. Try again in 15 minutes.')
        row = conn.execute("SELECT * FROM users WHERE phone=? AND status='ACTIVE'", (credentials.login.strip(),)).fetchone()
        active = row and (row['role'] != 'FRANCHISEE' or conn.execute("SELECT 1 FROM franchisees WHERE id=? AND status='ACTIVE'", (row['franchisee_id'],)).fetchone())
        if not active or not verify_password(credentials.password, row['pin']):
            conn.execute('INSERT INTO login_attempts(login,ip,created_at) VALUES(?,?,?)', (key, ip, now))
            conn.execute('DELETE FROM login_attempts WHERE created_at<?', (now-86400,))
            conn.commit()
            raise HTTPException(401, 'Invalid credentials')
        conn.execute('DELETE FROM login_attempts WHERE login=?', (key,))
        conn.execute('DELETE FROM revoked_access_tokens WHERE expires_at<?', (now,))
    token = jwt.encode({'sub': str(row['id']), 'role': row['role'], 'version': row['auth_version'], 'iat': int(now),
                        'exp': int(now)+TOKEN_SECONDS, 'iss': ISSUER, 'aud': AUDIENCE, 'jti': secrets.token_hex(16)},
                       session_secret(a.DATA_DIR), algorithm='HS256')
    return {'access_token': token, 'token_type': 'bearer', 'expires_in': TOKEN_SECONDS, 'role': row['role']}


def logout(request: Request):
    ops.require_user(request)
    claims = getattr(request.state, 'access_claims', None)
    if claims:
        with ops.a.get_conn() as conn:
            conn.execute('INSERT OR IGNORE INTO revoked_access_tokens VALUES(?,?)', (claims['jti'], claims['exp']))
    return {'signed_out': True}


def me(request: Request):
    user = ops.require_user(request)
    return {k: user[k] for k in ('id', 'name', 'role', 'outlet_id', 'franchisee_id')}


def orders(request: Request):
    user = ops.require_user(request)
    where, args = '1=1', ()
    if user['role'] == 'FRANCHISEE':
        where, args = 'o.franchisee_id=?', (user['franchisee_id'],)
    elif user['role'] in {'POS', 'GUARD'}:
        where, args = 'o.outlet_id=?', (user['outlet_id'],)
    elif user['role'] == 'DELIVERY':
        where = "EXISTS(SELECT 1 FROM delivery_tasks t LEFT JOIN bills b ON t.entity_type='BILL' AND b.id=t.entity_id LEFT JOIN docket_bills db ON t.entity_type='DOCKET' AND db.docket_id=t.entity_id LEFT JOIN bills bb ON bb.id=db.bill_id WHERE t.assigned_user_id=? AND (b.order_id=o.id OR bb.order_id=o.id))"
        args = (user['id'],)
    with ops.a.get_conn() as conn:
        rows = conn.execute(f'SELECT o.* FROM orders o WHERE {where} ORDER BY o.id DESC LIMIT 100', args).fetchall()
    return {'orders': [dict(r) for r in rows]}


def catalogue(request: Request):
    from app import dispatch_rules
    a = ops.a
    user = ops.require_user(request, {'FRANCHISEE'})
    with a.get_conn() as conn:
        cur = conn.cursor()
        franchise = cur.execute('SELECT * FROM franchisees WHERE id=?', (user['franchisee_id'],)).fetchone()
        products = []
        for row in cur.execute("SELECT * FROM products WHERE status='ACTIVE' ORDER BY id").fetchall():
            price, deal = a.resolve_deal(cur, row, row['rsp'], user['franchisee_id'])
            products.append(dict(row) | {'sale_price': price, 'deal_name': deal, 'commission_per_unit': round(a.resolve_commission(cur, row, price), 2)})
        return {'products': products, 'allowed_modes': sorted(dispatch_rules.allowed_modes(cur, franchise)), 'wallet_balance': franchise['wallet_balance']}


async def stream(request: Request):
    ops.require_user(request)
    async def updates():
        cursor = None
        while not await request.is_disconnected():
            try:
                if request.headers.get('authorization'):
                    await run_in_threadpool(token_user, request)
                feed = await run_in_threadpool(ops.event_feed, request, cursor or 0)
            except HTTPException:
                yield 'event: expired\ndata: {}\n\n'
                return
            if cursor is None or feed['events']:
                yield 'data: ' + json.dumps(feed | {'initial': cursor is None}) + '\n\n'
                cursor = feed['cursor']
            else:
                yield ': heartbeat\n\n'
            await asyncio.sleep(3)
    return StreamingResponse(updates(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-store'})


def adapt(handler):
    """Reuse the same validators/transactions; mobile mutations take form data."""
    async def mobile_handler(*args, **kwargs):
        request = kwargs.get('request')
        if request and not getattr(request.state, 'bearer_user', None):
            raise HTTPException(401, 'A bearer token is required')
        result = await handler(*args, **kwargs) if inspect.iscoroutinefunction(handler) else await run_in_threadpool(handler, *args, **kwargs)
        if isinstance(result, RedirectResponse):
            location = result.headers['location']
            if location.startswith('/login'):
                raise HTTPException(403, 'This action is not available for your role')
            if 'error=' in location:
                return JSONResponse({'ok': False, 'next': location}, status_code=409)
            return {'ok': True, 'next': location}
        return result
    mobile_handler.__signature__ = inspect.signature(handler)
    mobile_handler.__name__ = 'mobile_' + handler.__name__
    return mobile_handler


def register(a):
    a.app.add_api_route('/api/mobile/token', login, methods=['POST'])
    a.app.add_api_route('/api/mobile/logout', logout, methods=['POST'])
    a.app.add_api_route('/api/mobile/me', me, methods=['GET'])
    a.app.add_api_route('/api/mobile/orders', orders, methods=['GET'])
    a.app.add_api_route('/api/mobile/catalogue', catalogue, methods=['GET'])
    a.app.add_api_route('/api/events/stream', stream, methods=['GET'])
    paths = {'/franchise/orders', '/franchise/orders/{order_id}/execute', '/pos/orders/{order_id}/claim', '/pos/orders/{order_id}/scan', '/pos/bills', '/pos/bills/{bill_id}/pack', '/pos/dockets', '/pos/handover', '/guard/receive', '/guard/weight-check', '/guard/seal-check', '/guard/handover', '/delivery/tasks', '/delivery/tasks/{task_id}/proof', '/delivery/tasks/{task_id}/location', '/delivery/tasks/{task_id}/failed', '/franchise/dispatch-code'}
    for route in list(a.app.routes):
        if getattr(route, 'path', '') in paths and 'POST' in route.methods:
            a.app.add_api_route('/api/mobile' + route.path, adapt(route.endpoint), methods=['POST'], response_model=None)
