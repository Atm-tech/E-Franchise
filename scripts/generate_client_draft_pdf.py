from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DOCS.mkdir(parents=True, exist_ok=True)

PDF_PATH = DOCS / "E-Franchise-Client-Rough-Draft.pdf"
DIAGRAM_PATH = DOCS / "workflow-diagram.png"


def _font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


def draw_workflow_diagram(path: Path) -> None:
    w, h = 2000, 1200
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)

    title_font = _font(44)
    box_font = _font(24)
    small_font = _font(20)

    draw.text((60, 30), "E-Franchise Workflow Diagram", fill="black", font=title_font)

    def box(x1, y1, x2, y2, text, fill="#F4F7FF"):
        draw.rounded_rectangle((x1, y1, x2, y2), radius=20, outline="#1F3A93", width=4, fill=fill)
        tw = draw.multiline_textbbox((0, 0), text, font=box_font, align="center")
        text_w = tw[2] - tw[0]
        text_h = tw[3] - tw[1]
        tx = x1 + (x2 - x1 - text_w) / 2
        ty = y1 + (y2 - y1 - text_h) / 2
        draw.multiline_text((tx, ty), text, fill="black", font=box_font, align="center")

    def arrow(x1, y1, x2, y2, label=None):
        draw.line((x1, y1, x2, y2), fill="#303030", width=6)
        ah = 18
        if x2 >= x1:
            draw.polygon([(x2, y2), (x2 - ah, y2 - ah // 2), (x2 - ah, y2 + ah // 2)], fill="#303030")
        else:
            draw.polygon([(x2, y2), (x2 + ah, y2 - ah // 2), (x2 + ah, y2 + ah // 2)], fill="#303030")
        if label:
            lx = (x1 + x2) / 2
            ly = (y1 + y2) / 2 - 24
            draw.text((lx - 70, ly), label, fill="#202020", font=small_font)

    # Main row
    box(70, 150, 370, 290, "Admin")
    box(450, 150, 830, 290, "Franchisee App\n(Order + Mode)")
    box(860, 150, 1310, 290, "Outlet POS (Multi User)\nFloor Scan + Bill + Pack")
    box(1360, 150, 1740, 290, "Delivery Guard\nWeight Capture")
    box(1730, 150, 1970, 290, "Handover")

    arrow(370, 220, 450, 220, "Config")
    arrow(830, 220, 860, 220, "Order")
    arrow(1310, 220, 1360, 220, "Packed")
    arrow(1740, 220, 1730, 220)

    # Branches from handover
    box(1500, 420, 1950, 620, "Option A\nFranchise Pickup")
    box(980, 420, 1430, 620, "Option B\nDelivery Man")
    arrow(1850, 290, 1730, 420)
    arrow(1760, 290, 1220, 420)

    # Pickup branch end
    box(1500, 760, 1950, 1030, "Franchise Receives\nDocket/Bill\n(OTP Confirmed)")
    arrow(1730, 620, 1730, 760, "Pickup")

    # Delivery branch
    box(980, 760, 1430, 1030, "Delivery Man Picks\nFrom Guard and Delivers\n(POD Confirmed)")
    arrow(1210, 620, 1210, 760, "Dispatch")

    # Weight check decision
    box(540, 420, 880, 620, "Weight Check\nWithin Tolerance?")
    arrow(1360, 220, 880, 500, "Check")
    draw.text((570, 635), "No -> Recheck qty/items at POS", fill="#202020", font=small_font)

    # Side note
    note = (
        "Multi-bill flow:\n"
        "POS packs each bill individually,\n"
        "bundles under one docket,\n"
        "guard hands over docket."
    )
    draw.rounded_rectangle((70, 420, 820, 710), radius=16, outline="#666", width=3, fill="#FFF9E8")
    draw.multiline_text((100, 460), note, fill="#222", font=small_font, spacing=8)

    img.save(path)


def _paragraph_lines(text: str):
    return [line.strip() for line in text.strip().splitlines()]


def generate_pdf(pdf_path: Path, diagram_path: Path) -> None:
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    width, height = A4

    def title(text, y):
        c.setFont("Helvetica-Bold", 18)
        c.drawString(2 * cm, y, text)
        return y - 0.8 * cm

    def heading(text, y):
        c.setFont("Helvetica-Bold", 12)
        c.drawString(2 * cm, y, text)
        return y - 0.55 * cm

    def bullets(lines, y):
        c.setFont("Helvetica", 10.5)
        for line in lines:
            c.drawString(2.3 * cm, y, f"- {line}")
            y -= 0.45 * cm
        return y - 0.1 * cm

    y = height - 2 * cm
    y = title("E-Franchise Model: Client Rough Draft", y)
    c.setFont("Helvetica", 10)
    c.drawString(2 * cm, y, "Draft Date: 16 Feb 2026")
    y -= 0.8 * cm

    y = heading("1) Vision", y)
    y = bullets(
        _paragraph_lines(
            """
            Build an integrated B2B franchise commerce platform connecting Admin, Franchisee, Outlet POS, Delivery Guard, and Delivery Man.
            Start with web-first operations and configurable controls; later release Android APK and iOS apps on the same backend.
            """
        ),
        y,
    )

    y = heading("2) Core Modules", y)
    y = bullets(
        _paragraph_lines(
            """
            Admin: onboarding, product catalog, commissions, special deals, delivery rules, user/role controls.
            Franchisee: place order, choose instant/day delivery, choose pickup/delivery mode, track bill/docket status.
            Outlet POS: multiple POS users per outlet, floor scan-and-bill, create docket (single or multi-bill), handover to guard.
            Delivery Guard: custody management, weight capture, tolerance alert, verified handover to franchise or delivery man.
            Delivery Man: pickup from guard, navigation, proof-of-delivery.
            """
        ),
        y,
    )

    y = heading("3) Financial Logic", y)
    y = bullets(
        _paragraph_lines(
            """
            Franchise advance received by admin; 75% credited into franchise wallet.
            Order execution allowed only when payable value is less than or equal to wallet balance.
            Commission configurable at department or product level; product-level rule overrides department-level.
            Payable to outlet = bill value minus commission.
            """
        ),
        y,
    )

    y = heading("4) Dispatch and Docket Logic", y)
    y = bullets(
        _paragraph_lines(
            """
            Single bill can move directly by bill number or optional single-bill docket.
            Multiple bills for one franchise are packed individually and grouped into one docket.
            Docket number is mapped to franchise and visible in franchise app along with all bill numbers.
            POS can handover all bills or selected bills; guard captures weight at bill/docket level.
            If measured weight exceeds tolerance, system alerts for quantity/item recheck before dispatch.
            Final handover may be to franchise pickup or via delivery man based on franchise selection.
            """
        ),
        y,
    )

    y = heading("5) Client Benefits", y)
    y = bullets(
        _paragraph_lines(
            """
            Full traceability of custody from POS to final recipient.
            Reduced dispatch errors through docket mapping and handover confirmation.
            Commercial flexibility via admin-configurable commissions, deals, and delivery pricing.
            Scalable architecture ready for mobile launch in next phase.
            """
        ),
        y,
    )

    y = heading("6) Proposed Phasing", y)
    y = bullets(
        _paragraph_lines(
            """
            Phase 1: Admin + Wallet + Order + POS + Guard + Docket + Delivery operations.
            Phase 1: includes manual weight-entry validation with tolerance alerts at guard desk.
            Phase 2: payment gateway, UPI top-up, GST workflows, geofencing, weighing machine and counting machine integration, APK/iOS deployment.
            """
        ),
        y,
    )

    c.showPage()
    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, height - 2 * cm, "Operational Workflow Diagram")
    c.setFont("Helvetica", 10)
    c.drawString(2 * cm, height - 2.7 * cm, "Single-bill and multi-bill dispatch flows")
    c.drawImage(str(diagram_path), 1.2 * cm, 2.2 * cm, width=width - 2.4 * cm, height=height - 5.5 * cm, preserveAspectRatio=True, anchor='c')

    c.save()


if __name__ == "__main__":
    draw_workflow_diagram(DIAGRAM_PATH)
    generate_pdf(PDF_PATH, DIAGRAM_PATH)
    print(f"Generated: {PDF_PATH}")
    print(f"Generated: {DIAGRAM_PATH}")
