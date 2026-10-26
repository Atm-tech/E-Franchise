"""Auth HTTP routes."""
from fastapi import APIRouter

# Shared application services stay in app.main during the route extraction.
from app.core import *  # noqa: F401,F403

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def root() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
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


@router.post('/testing/quick-login', include_in_schema=False)
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


@router.post("/login")
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


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
