import sqlite3
from pathlib import Path

from playwright.sync_api import sync_playwright
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SHOT_DIR = DOCS / "real-app-shots"
DB_PATH = ROOT / "data" / "e_franchise.db"
PDF_PATH = DOCS / "E-Franchise-Real-App-Screenshots.pdf"
BASE_URL = "http://127.0.0.1:8000"

SHOT_DIR.mkdir(parents=True, exist_ok=True)


def login(page, username: str, password: str) -> None:
    page.goto(f"{BASE_URL}/login", wait_until="networkidle")
    page.fill('input[name="phone"]', username)
    page.fill('input[name="pin"]', password)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")


def save_screenshot(page, name: str) -> Path:
    path = SHOT_DIR / name
    page.screenshot(path=str(path), full_page=True)
    return path


def get_latest_bill() -> tuple[int, float] | tuple[None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT id, expected_weight_kg FROM bills ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if not row:
        return None, None
    return int(row["id"]), float(row["expected_weight_kg"] or 0)


def get_latest_task() -> int | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT id FROM delivery_tasks ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return int(row["id"]) if row else None


def capture_screens() -> list[tuple[str, Path]]:
    shots: list[tuple[str, Path]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # Admin (desktop)
        admin_ctx = browser.new_context(viewport={"width": 1366, "height": 900})
        admin = admin_ctx.new_page()
        login(admin, "adm", "adm@123")
        shots.append(("Admin Dashboard (Desktop)", save_screenshot(admin, "01-admin-dashboard.png")))
        admin_ctx.close()

        # Franchise (mobile)
        fr_ctx = browser.new_context(viewport={"width": 430, "height": 932})
        fr = fr_ctx.new_page()
        login(fr, "frn", "frn@123")
        shots.append(("Franchise Dashboard (Mobile)", save_screenshot(fr, "02-franchise-dashboard.png")))
        fr.click('button[data-tab="order-tab"]')
        fr.wait_for_timeout(600)
        shots.append(("Franchise Order Section (Mobile)", save_screenshot(fr, "03-franchise-order.png")))
        fr.click(".product-card .add-btn")
        fr.wait_for_load_state("networkidle")
        shots.append(("Franchise Order In Action (Order Created)", save_screenshot(fr, "04-franchise-order-created.png")))
        fr_ctx.close()

        # POS (mobile)
        pos_ctx = browser.new_context(viewport={"width": 430, "height": 932})
        pos = pos_ctx.new_page()
        login(pos, "pos", "pos@123")
        shots.append(("POS Module (Mobile)", save_screenshot(pos, "05-pos-home.png")))

        make_bill_buttons = pos.locator('form[action="/pos/bills"] button')
        if make_bill_buttons.count() > 0:
            make_bill_buttons.first.click()
            pos.wait_for_load_state("networkidle")

        shots.append(("POS Billing In Action", save_screenshot(pos, "06-pos-billed.png")))

        to_guard_buttons = pos.locator('form[action="/pos/handover"] button')
        if to_guard_buttons.count() > 0:
            to_guard_buttons.first.click()
            pos.wait_for_load_state("networkidle")

        shots.append(("POS to Guard Handover", save_screenshot(pos, "07-pos-to-guard.png")))
        pos_ctx.close()

        bill_id, expected = get_latest_bill()

        # Guard (mobile)
        guard_ctx = browser.new_context(viewport={"width": 430, "height": 932})
        guard = guard_ctx.new_page()
        login(guard, "grd", "grd@123")
        shots.append(("Guard Module (Mobile)", save_screenshot(guard, "08-guard-home.png")))

        if bill_id is not None:
            guard.select_option('select[name="entity_type"]', "BILL")
            guard.fill('input[name="entity_id"]', str(bill_id))
            guard.fill('input[name="actual_weight_kg"]', f"{expected:.3f}")
            guard.click('form[action="/guard/weight-check"] button')
            guard.wait_for_load_state("networkidle")
            shots.append(("Guard Weight Check In Action", save_screenshot(guard, "09-guard-weight-check.png")))

            guard.select_option('form[action="/guard/handover"] select[name="entity_type"]', "BILL")
            guard.fill('form[action="/guard/handover"] input[name="entity_id"]', str(bill_id))
            guard.select_option('form[action="/guard/handover"] select[name="target_role"]', "DELIVERY")
            guard.click('form[action="/guard/handover"] button')
            guard.wait_for_load_state("networkidle")
            shots.append(("Guard to Delivery Handover", save_screenshot(guard, "10-guard-to-delivery.png")))
        guard_ctx.close()

        # Delivery (mobile)
        dl_ctx = browser.new_context(viewport={"width": 430, "height": 932})
        dlv = dl_ctx.new_page()
        login(dlv, "dlv", "dlv@123")
        shots.append(("Delivery Module (Mobile)", save_screenshot(dlv, "11-delivery-home.png")))

        task_id = get_latest_task()
        if task_id is not None:
            start_btns = dlv.locator('form[action="/delivery/tasks"] button:has-text("Start")')
            if start_btns.count() > 0:
                start_btns.first.click()
                dlv.wait_for_load_state("networkidle")
            shots.append(("Delivery In Action (Out for Delivery)", save_screenshot(dlv, "12-delivery-started.png")))

            delivered_btns = dlv.locator('form[action="/delivery/tasks"] button:has-text("Mark Delivered")')
            if delivered_btns.count() > 0:
                delivered_btns.first.click()
                dlv.wait_for_load_state("networkidle")
            shots.append(("Delivery Completed", save_screenshot(dlv, "13-delivery-completed.png")))
        dl_ctx.close()
        browser.close()
    return shots


def make_pdf(shots: list[tuple[str, Path]]) -> None:
    c = canvas.Canvas(str(PDF_PATH), pagesize=A4)
    page_w, page_h = A4
    margin = 28

    # Cover
    c.setFont("Helvetica-Bold", 20)
    c.drawString(margin, page_h - 70, "E-Franchise Real App Module Screenshots")
    c.setFont("Helvetica", 11)
    c.drawString(margin, page_h - 92, "Captured from the running MVP application")
    c.drawString(margin, page_h - 110, "Flow: Admin -> Franchise -> POS -> Guard -> Delivery")
    c.showPage()

    for title, img_path in shots:
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margin, page_h - 40, title)

        img = ImageReader(str(img_path))
        iw, ih = img.getSize()

        max_w = page_w - (2 * margin)
        max_h = page_h - 100
        ratio = min(max_w / iw, max_h / ih)
        rw, rh = iw * ratio, ih * ratio
        x = (page_w - rw) / 2
        y = (page_h - rh) / 2 - 18
        c.drawImage(str(img_path), x, y, width=rw, height=rh)
        c.showPage()

    c.save()


if __name__ == "__main__":
    captures = capture_screens()
    make_pdf(captures)
    print(f"Generated: {PDF_PATH}")
    print(f"Screenshots dir: {SHOT_DIR}")
    for title, path in captures:
        print(f"- {title}: {path.name}")
