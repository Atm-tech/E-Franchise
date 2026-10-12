"""Password storage and request protections shared by the web modules."""
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


def hash_password(value: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', value.encode(), salt.encode(), 310000).hex()
    return f'pbkdf2_sha256$310000${salt}${digest}'


def verify_password(value: str, encoded: str) -> bool:
    try:
        scheme, rounds, salt, digest = encoded.split('$')
        if scheme != 'pbkdf2_sha256':
            return False
        candidate = hashlib.pbkdf2_hmac('sha256', value.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(candidate, digest)
    except (ValueError, TypeError):
        return False


def session_secret(data_dir: Path) -> str:
    configured = os.getenv('SESSION_SECRET', '')
    if configured:
        if len(configured) < 32 or configured.startswith('replace-'):
            raise RuntimeError('SESSION_SECRET must contain at least 32 characters')
        return configured
    if os.getenv('APP_ENV') == 'production':
        raise RuntimeError('Set SESSION_SECRET before production startup')
    path = data_dir / '.session-secret'
    try:
        with path.open('x') as stream:
            stream.write(secrets.token_urlsafe(48))
        path.chmod(0o600)
    except FileExistsError:
        pass
    return path.read_text().strip()


class RequestProtection(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.state.bearer_user = None
        if request.headers.get('authorization'):
            from app.mobile_api import token_user
            from fastapi import HTTPException
            try:
                request.state.bearer_user = token_user(request)
            except HTTPException as exc:
                return JSONResponse({'detail': exc.detail}, status_code=exc.status_code, headers=exc.headers)
        token = request.session.setdefault('csrf_token', secrets.token_urlsafe(32))
        external = request.url.path in {'/integrations/razorpay/webhook', '/integrations/device/reading'}
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            try:
                if int(request.headers.get('content-length','0')) > 8 * 1024 * 1024:
                    return JSONResponse({'detail':'Upload limit is 8 MB'},status_code=413)
            except ValueError:
                return JSONResponse({'detail':'Invalid content length'},status_code=400)
            body = await request.body()
            if len(body) > 8 * 1024 * 1024:
                return JSONResponse({'detail': 'Upload limit is 8 MB'}, status_code=413)
            mobile_login = request.url.path == '/api/mobile/token'
            if mobile_login and request.headers.get('origin') and request.headers['origin'].rstrip('/') != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail': 'Cross-origin login is not allowed'}, status_code=403)
            if not external and not mobile_login and not request.state.bearer_user:
                supplied = request.headers.get('x-csrf-token', '')
                if not supplied:
                    form = await request.form()
                    supplied = str(form.get('csrf_token', ''))
                if not supplied or not hmac.compare_digest(token, supplied):
                    return JSONResponse({'detail': 'Session expired. Reload the page and try again.'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        if not request.url.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store'
        if os.getenv('APP_ENV') == 'production':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response
