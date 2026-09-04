from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "Aapoorti-E-Franchise-Complete-Workflow.pdf"
GREEN = colors.HexColor("#0B4F3C")
GREEN_2 = colors.HexColor("#16765A")
LIME = colors.HexColor("#C9F05B")
CREAM = colors.HexColor("#F5F4ED")
INK = colors.HexColor("#15231E")
MUTED = colors.HexColor("#68766F")
LINE = colors.HexColor("#DDE4DF")
PALE_GREEN = colors.HexColor("#EAF4EE")
PALE_ORANGE = colors.HexColor("#FFF1DB")
RED = colors.HexColor("#A43C30")


def register_fonts() -> tuple[str, str]:
    candidates = [
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
        (Path("C:/Windows/Fonts/segoeui.ttf"), Path("C:/Windows/Fonts/segoeuib.ttf")),
    ]
    for regular, bold in candidates:
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("AppRegular", str(regular)))
            pdfmetrics.registerFont(TTFont("AppBold", str(bold)))
            return "AppRegular", "AppBold"
    return "Helvetica", "Helvetica-Bold"


REGULAR, BOLD = register_fonts()


def pstyle(name: str, **kwargs) -> ParagraphStyle:
    kwargs.setdefault("fontName", REGULAR)
    kwargs.setdefault("textColor", INK)
    kwargs.setdefault("leading", 14)
    return ParagraphStyle(name, **kwargs)


styles = {
    "cover_brand": pstyle("cover_brand", fontName=BOLD, fontSize=13, textColor=LIME, leading=16, spaceAfter=9),
    "cover_title": pstyle("cover_title", fontName=BOLD, fontSize=29, textColor=colors.white, leading=34, spaceAfter=14),
    "cover_sub": pstyle("cover_sub", fontSize=13, textColor=colors.HexColor("#D1E2DB"), leading=19),
    "h1": pstyle("h1", fontName=BOLD, fontSize=22, textColor=GREEN, leading=27, spaceAfter=12),
    "h2": pstyle("h2", fontName=BOLD, fontSize=14, textColor=GREEN, leading=18, spaceBefore=9, spaceAfter=7),
    "h3": pstyle("h3", fontName=BOLD, fontSize=10.5, textColor=INK, leading=14, spaceAfter=3),
    "body": pstyle("body", fontSize=9.5, leading=14, spaceAfter=7),
    "small": pstyle("small", fontSize=8, textColor=MUTED, leading=11),
    "bullet": pstyle("bullet", fontSize=9.2, leading=13, leftIndent=12, firstLineIndent=-8, bulletIndent=2, spaceAfter=4),
    "callout": pstyle("callout", fontName=BOLD, fontSize=11, textColor=GREEN, leading=16, alignment=TA_CENTER),
    "table_head": pstyle("table_head", fontName=BOLD, fontSize=8, textColor=colors.white, leading=10),
    "table": pstyle("table", fontSize=7.8, leading=10.5),
    "table_bold": pstyle("table_bold", fontName=BOLD, fontSize=7.8, leading=10.5),
}


class StatusPill(Flowable):
    def __init__(self, text: str, width: float = 88 * mm):
        super().__init__()
        self.text = text
        self.width = width
        self.height = 12 * mm

    def draw(self):
        self.canv.setFillColor(PALE_GREEN)
        self.canv.roundRect(0, 0, self.width, self.height, 4 * mm, fill=1, stroke=0)
        self.canv.setFillColor(GREEN)
        self.canv.setFont(BOLD, 9)
        self.canv.drawCentredString(self.width / 2, 4.1 * mm, self.text)


def header_footer(canvas, doc):
    canvas.saveState()
    width, height = doc.pagesize
    canvas.setFillColor(GREEN)
    canvas.rect(0, height - 15 * mm, width, 15 * mm, fill=1, stroke=0)
    canvas.setFillColor(LIME)
    canvas.setFont(BOLD, 10)
    canvas.drawString(16 * mm, height - 9.7 * mm, "AAPOORTI  ·  E-FRANCHISE")
    canvas.setFillColor(colors.white)
    canvas.setFont(REGULAR, 8)
    canvas.drawRightString(width - 16 * mm, height - 9.7 * mm, "Complete Operational Workflow")
    canvas.setStrokeColor(LINE)
    canvas.line(16 * mm, 13 * mm, width - 16 * mm, 13 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont(REGULAR, 7.5)
    canvas.drawString(16 * mm, 8 * mm, "Internal workflow reference · September 2026")
    canvas.drawRightString(width - 16 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def cover_page(canvas, doc):
    canvas.saveState()
    width, height = doc.pagesize
    canvas.setFillColor(GREEN)
    canvas.rect(0, 0, width, height, fill=1, stroke=0)
    canvas.setFillColor(GREEN_2)
    canvas.circle(width - 25 * mm, height - 25 * mm, 62 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#236D57"))
    canvas.circle(8 * mm, 12 * mm, 55 * mm, fill=1, stroke=0)
    canvas.setFillColor(LIME)
    canvas.roundRect(20 * mm, height - 50 * mm, 22 * mm, 22 * mm, 6 * mm, fill=1, stroke=0)
    canvas.setFillColor(GREEN)
    canvas.setFont(BOLD, 18)
    canvas.drawCentredString(31 * mm, height - 42.4 * mm, "A")
    canvas.restoreState()


def bullet(text: str) -> Paragraph:
    return Paragraph(f"• {text}", styles["bullet"])


def info_box(title: str, text: str, background=PALE_GREEN) -> Table:
    content = [[Paragraph(title, styles["h3"])], [Paragraph(text, styles["body"])]]
    table = Table(content, colWidths=[170 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), background),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def role_card(role: str, responsibility: str, output: str) -> list:
    return [
        Paragraph(role, styles["table_bold"]),
        Paragraph(responsibility, styles["table"]),
        Paragraph(output, styles["table"]),
    ]


def build_story() -> list:
    story = []

    story += [Spacer(1, 58 * mm), Paragraph("AAPOORTI PARTNER NETWORK", styles["cover_brand"])]
    story += [Paragraph("E‑Franchise<br/>Complete Workflow", styles["cover_title"])]
    story += [Paragraph("From network setup and customer order capture to franchise pickup or delivery at the franchisee’s registered address.", styles["cover_sub"])]
    story += [Spacer(1, 28 * mm), StatusPill("CUSTOMER-HOME DELIVERY IS NOT PERMITTED", 102 * mm)]
    story += [Spacer(1, 7 * mm), Paragraph("Operational baseline · Admin · Franchisee · Outlet POS · Guard · Delivery", styles["cover_sub"]), PageBreak()]

    story += [Paragraph("1. The operating model", styles["h1"])]
    story += [Paragraph(
        "Aapoorti is an assisted-commerce network. The franchisee records a customer’s purchase, while the assigned outlet stocks, bills, packs and dispatches the goods. The customer is the buyer record—not the delivery destination.",
        styles["body"],
    )]
    story += [info_box(
        "The non-negotiable fulfilment rule",
        "Every parcel follows exactly one of two paths: <b>(A) Franchise pickup</b> from the assigned outlet, or <b>(B) Aapoorti delivery</b> to the franchisee’s registered delivery address. The delivery partner never travels to the customer’s home.",
    ), Spacer(1, 7 * mm)]

    story += [Paragraph("Who does what", styles["h2"])]
    role_rows = [[Paragraph("ROLE", styles["table_head"]), Paragraph("PRIMARY RESPONSIBILITY", styles["table_head"]), Paragraph("HANDOFF / RESULT", styles["table_head"])]]
    role_rows += [role_card("Admin", "Creates outlets, users, franchise profiles, wallet rules, products, commissions, promotions and compliance settings.", "A configured and auditable network.")]
    role_rows += [role_card("Franchisee", "Records the customer, builds the basket, chooses pickup or franchise delivery, and executes through the wallet.", "An ON_HOLD or EXECUTED order.")]
    role_rows += [role_card("Outlet POS", "Bills, picks, individually packs and optionally groups same-franchise bills into a docket.", "A packed bill or docket at the guard desk.")]
    role_rows += [role_card("Delivery Guard", "Controls custody, verifies measured weight and releases the parcel to the correct receiver.", "Franchise pickup or delivery assignment.")]
    role_rows += [role_card("Delivery Partner", "Accepts, picks up, travels to the registered franchise address and captures franchise proof.", "Delivered order with OTP/signature/photo reference.")]
    table = Table(role_rows, colWidths=[27 * mm, 91 * mm, 52 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREEN), ("GRID", (0, 0), (-1, -1), 0.45, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CREAM]),
    ]))
    story += [table, PageBreak()]

    story += [Paragraph("2. End-to-end journey", styles["h1"])]
    flow = [
        ("1", "ADMIN SETUP", "Outlet, roles, franchise destination, wallet and commercial rules"),
        ("2", "CUSTOMER ORDER", "Franchisee records buyer and builds the basket"),
        ("3", "WALLET GATE", "Execute if funded; otherwise hold safely"),
        ("4", "OUTLET POS", "Bill → pick → pack individually → optional docket"),
        ("5", "GUARD", "Receive custody → verify weight → approve release"),
        ("6A", "FRANCHISE PICKUP", "Franchisee collects from assigned outlet"),
        ("6B", "FRANCHISE DELIVERY", "Delivery partner takes parcel to registered franchise address"),
        ("7", "COMPLETION", "Proof, delivered status, ledger and audit trail"),
    ]
    flow_rows = []
    for number, title, detail in flow:
        flow_rows.append([
            Paragraph(number, pstyle(f"num{number}", fontName=BOLD, fontSize=11, textColor=GREEN, alignment=TA_CENTER)),
            Paragraph(title, styles["table_bold"]),
            Paragraph(detail, styles["table"]),
        ])
    flow_table = Table(flow_rows, colWidths=[16 * mm, 45 * mm, 109 * mm])
    flow_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (0, -1), PALE_GREEN), ("ROWBACKGROUNDS", (1, 0), (-1, -1), [colors.white, CREAM]),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story += [flow_table, Spacer(1, 8 * mm)]
    story += [info_box("Decision at the guard desk", "<b>Pickup selected:</b> release only to the franchisee. &nbsp;&nbsp; <b>Delivery selected:</b> create a delivery task addressed to the franchisee’s registered destination. Dockets cannot mix these fulfilment modes.", PALE_ORANGE), PageBreak()]

    story += [Paragraph("3. Detailed workflow", styles["h1"])]
    sections = [
        ("A. Admin prepares the network", [
            "Create the outlet and its POS, Guard and Delivery users.",
            "Onboard the franchisee with shop, banking, mapped outlet and registered delivery address.",
            "Record the advance; the configured share (default 75%) becomes wallet credit.",
            "Publish product prices, unit weights, pack multipliers, commissions and promotions.",
            "Set delivery price, free-delivery quota, weight tolerance and alert-blocking policy.",
        ]),
        ("B. Franchisee records the sale", [
            "Record customer name and mobile only as the buyer identity.",
            "Build the basket and review price, promotion, commission and delivery cost.",
            "Choose Franchise Pickup or Deliver to Franchise.",
            "For delivery, the backend always uses the registered franchise delivery address—even if another address is submitted manually.",
        ]),
        ("C. Wallet authorizes execution", [
            "If the wallet covers the total, debit exactly once and mark EXECUTED.",
            "If funding is insufficient, save ON_HOLD; the outlet cannot see or pack it.",
            "After wallet credit, execute the held order. A permitted cancellation refunds exactly once.",
        ]),
        ("D. Outlet fulfils", [
            "Only EXECUTED orders appear in the POS queue.",
            "Generate one bill per order, pick from the floor and mark it PACKED_INDIVIDUALLY.",
            "Send a single bill to the guard, or group packed bills from the same franchise and same fulfilment mode into one docket.",
        ]),
        ("E. Guard verifies and releases", [
            "Accept custody from POS and compare measured weight with system-calculated expected weight.",
            "PASS when within tolerance; ALERT when outside tolerance. Blocking alerts cannot be dispatched.",
            "Release to the franchisee for pickup or to the assigned delivery partner for franchise delivery.",
        ]),
        ("F. Delivery completes", [
            "Accept assignment → confirm pickup from guard → start trip.",
            "Navigate to the registered franchise delivery address and call the franchisee—not the customer.",
            "Capture franchisee OTP, signature or handover-photo reference, then mark DELIVERED.",
        ]),
    ]
    for heading, bullets in sections:
        block = [Paragraph(heading, styles["h2"])] + [bullet(item) for item in bullets]
        story.append(KeepTogether(block))
    story.append(PageBreak())

    story += [Paragraph("4. Status and custody map", styles["h1"])]
    status_rows = [[Paragraph("STAGE", styles["table_head"]), Paragraph("STATUS", styles["table_head"]), Paragraph("CUSTODY / MEANING", styles["table_head"])],
        [Paragraph("Order", styles["table_bold"]), Paragraph("ON_HOLD", styles["table"]), Paragraph("Not funded; invisible to outlet operations.", styles["table"])],
        [Paragraph("Order", styles["table_bold"]), Paragraph("EXECUTED", styles["table"]), Paragraph("Wallet debited; outlet may start fulfilment.", styles["table"])],
        [Paragraph("POS", styles["table_bold"]), Paragraph("BILLED → PACKED_INDIVIDUALLY", styles["table"]), Paragraph("Outlet owns and prepares the parcel.", styles["table"])],
        [Paragraph("Docket", styles["table_bold"]), Paragraph("DOCKET_CREATED", styles["table"]), Paragraph("Compatible packed bills grouped into one load.", styles["table"])],
        [Paragraph("Guard", styles["table_bold"]), Paragraph("AT_GUARD_DESK", styles["table"]), Paragraph("Custody transferred from POS to guard.", styles["table"])],
        [Paragraph("Guard", styles["table_bold"]), Paragraph("WEIGHT_CHECK_PASSED / ALERT", styles["table"]), Paragraph("Parcel verified or held for recheck.", styles["table"])],
        [Paragraph("Pickup", styles["table_bold"]), Paragraph("PICKED_BY_FRANCHISEE", styles["table"]), Paragraph("Franchisee received parcel from outlet guard.", styles["table"])],
        [Paragraph("Delivery", styles["table_bold"]), Paragraph("ASSIGNED → ACCEPTED → PICKED_UP → OUT_FOR_DELIVERY", styles["table"]), Paragraph("Delivery partner carries parcel to franchise destination.", styles["table"])],
        [Paragraph("Complete", styles["table_bold"]), Paragraph("DELIVERED", styles["table"]), Paragraph("Franchise proof captured and order closed.", styles["table"])],
    ]
    status_table = Table(status_rows, colWidths=[27 * mm, 63 * mm, 80 * mm], repeatRows=1)
    status_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREEN), ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CREAM]),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story += [status_table, Spacer(1, 7 * mm)]
    story += [info_box("Custody principle", "At every moment the system can identify who controls the parcel: Outlet POS, Guard, Franchisee or Delivery Partner. Every handover creates an audit record.")]
    story.append(PageBreak())

    story += [Paragraph("5. Exception paths and controls", styles["h1"])]
    exception_rows = [[Paragraph("SITUATION", styles["table_head"]), Paragraph("SYSTEM RESPONSE", styles["table_head"]), Paragraph("WHY IT IS SAFE", styles["table_head"])],
        [Paragraph("Insufficient wallet", styles["table_bold"]), Paragraph("Force ON_HOLD; do not expose to POS.", styles["table"]), Paragraph("No unfunded fulfilment begins.", styles["table"])],
        [Paragraph("Execute held order", styles["table_bold"]), Paragraph("Recheck active status and live balance before one debit.", styles["table"]), Paragraph("Prevents negative and duplicate debits.", styles["table"])],
        [Paragraph("Cancel executed order", styles["table_bold"]), Paragraph("Allow only before billing and create one wallet refund.", styles["table"]), Paragraph("Protects financial and physical consistency.", styles["table"])],
        [Paragraph("Weight variance", styles["table_bold"]), Paragraph("PASS or ALERT against configured tolerance.", styles["table"]), Paragraph("Catches likely quantity/item mismatch.", styles["table"])],
        [Paragraph("Blocking alert", styles["table_bold"]), Paragraph("Disable guard handover until corrected.", styles["table"]), Paragraph("Bad parcels cannot leave custody.", styles["table"])],
        [Paragraph("Mixed docket", styles["table_bold"]), Paragraph("Reject different franchisees or fulfilment modes.", styles["table"]), Paragraph("One docket has one destination and receiver.", styles["table"])],
        [Paragraph("Customer address submitted", styles["table_bold"]), Paragraph("Ignore it and use registered franchise delivery address.", styles["table"]), Paragraph("Customer-home delivery remains impossible.", styles["table"])],
        [Paragraph("Missing delivery proof", styles["table_bold"]), Paragraph("Block DELIVERED status until proof reference is entered.", styles["table"]), Paragraph("Final handover remains accountable.", styles["table"])],
    ]
    exception_table = Table(exception_rows, colWidths=[38 * mm, 75 * mm, 57 * mm], repeatRows=1)
    exception_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREEN), ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CREAM]),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story += [exception_table, Spacer(1, 8 * mm)]
    story += [Paragraph("What remains traceable", styles["h2"])]
    for item in [
        "Advance receipts, wallet credits, order debits, adjustments and refunds.",
        "Order creation, execution, packing, docketing, weight results and completion.",
        "Every custody handover with actor, role, entity, timestamp and notes.",
        "Delivery assignment stages and franchisee proof of receipt.",
    ]:
        story.append(bullet(item))

    story += [Spacer(1, 7 * mm), info_box(
        "Final outcome",
        "The customer buys through the franchisee. The outlet fulfils the order. The parcel ends with the franchisee—either by outlet pickup or delivery to the franchisee’s registered address—and the system preserves the complete financial and custody history.",
    )]
    return story


def generate() -> Path:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(OUTPUT), pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm,
        topMargin=23 * mm, bottomMargin=18 * mm, title="Aapoorti E-Franchise Complete Workflow",
        author="Aapoorti", subject="Complete operational workflow",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[frame], onPage=cover_page),
        PageTemplate(id="content", frames=[frame], onPage=header_footer),
    ])

    story = build_story()
    # The first page uses the cover; following pages use the content template.
    from reportlab.platypus import NextPageTemplate
    story.insert(story.index(next(x for x in story if isinstance(x, PageBreak))), NextPageTemplate("content"))
    doc.build(story)
    return OUTPUT


if __name__ == "__main__":
    result = generate()
    print(result)
