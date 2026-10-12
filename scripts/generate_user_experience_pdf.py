from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Image, Table,
                                TableStyle, PageBreak, KeepTogether)
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from PIL import Image as PILImage
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/pdf/Aapoorti-E-Franchise-User-Experience.pdf'
SHOTS=ROOT/'docs/real-app-shots'
GREEN=colors.HexColor('#0b4f3c'); DARK=colors.HexColor('#17332b'); MUTED=colors.HexColor('#61736c')
PALE=colors.HexColor('#eaf4ef'); LINE=colors.HexColor('#dbe6e0'); GOLD=colors.HexColor('#f2b849')

font_dir=Path('C:/Windows/Fonts')
for name,file in [('DejaVu','arial.ttf'),('DejaVu-Bold','arialbd.ttf')]:
    p=font_dir/file
    if p.exists(): pdfmetrics.registerFont(TTFont(name,str(p)))
FONT='DejaVu' if 'DejaVu' in pdfmetrics.getRegisteredFontNames() else 'Helvetica'
BOLD='DejaVu-Bold' if 'DejaVu-Bold' in pdfmetrics.getRegisteredFontNames() else 'Helvetica-Bold'

styles=getSampleStyleSheet()
styles.add(ParagraphStyle(name='TitleX',fontName=BOLD,fontSize=29,leading=34,textColor=GREEN,spaceAfter=10))
styles.add(ParagraphStyle(name='SubtitleX',fontName=FONT,fontSize=13,leading=19,textColor=MUTED,spaceAfter=12))
styles.add(ParagraphStyle(name='H1X',fontName=BOLD,fontSize=21,leading=25,textColor=GREEN,spaceBefore=2,spaceAfter=8))
styles.add(ParagraphStyle(name='H2X',fontName=BOLD,fontSize=13,leading=17,textColor=DARK,spaceBefore=6,spaceAfter=4))
styles.add(ParagraphStyle(name='BodyX',fontName=FONT,fontSize=9.2,leading=13.3,textColor=DARK,spaceAfter=5))
styles.add(ParagraphStyle(name='SmallX',fontName=FONT,fontSize=7.5,leading=10,textColor=MUTED))
styles.add(ParagraphStyle(name='StepX',fontName=FONT,fontSize=8.5,leading=12,textColor=DARK))
styles.add(ParagraphStyle(name='KickerX',fontName=BOLD,fontSize=8,leading=10,textColor=GREEN,spaceAfter=5))

def P(txt,style='BodyX'): return Paragraph(txt,styles[style])
def shot(name,width):
    p=SHOTS/name
    with PILImage.open(p) as im: w,h=im.size
    return Image(str(p),width=width,height=width*h/w)
def page_head(kicker,title,desc):
    return [P(kicker.upper(),'KickerX'),P(title,'H1X'),P(desc,'BodyX'),Spacer(1,4)]
def steps(items):
    data=[]
    for i,(head,body) in enumerate(items,1):
        data.append([P(f'<b>{i:02d}</b>','StepX'),P(f'<b>{head}</b><br/>{body}','StepX')])
    t=Table(data,colWidths=[13*mm,150*mm],hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(0,-1),PALE),('TEXTCOLOR',(0,0),(0,-1),GREEN),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),('LINEBELOW',(0,0),(-1,-2),.4,LINE)]))
    return t
def note(text):
    t=Table([[P(text,'BodyX')]],colWidths=[163*mm])
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),PALE),('BOX',(0,0),(-1,-1),.5,LINE),('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
    return t

def footer(canvas,doc):
    canvas.saveState(); w,h=A4
    canvas.setStrokeColor(LINE); canvas.line(23*mm,17*mm,w-23*mm,17*mm)
    canvas.setFont(FONT,7.5); canvas.setFillColor(MUTED)
    canvas.drawString(23*mm,11*mm,'AAPOORTI  /  E-FRANCHISE  /  USER EXPERIENCE')
    canvas.drawRightString(w-23*mm,11*mm,f'{doc.page}')
    canvas.restoreState()

class GuideDocTemplate(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable,PageBreak):
            self._nextPageTemplate='Later'

doc=GuideDocTemplate(str(OUT),pagesize=A4,rightMargin=23*mm,leftMargin=23*mm,topMargin=20*mm,bottomMargin=24*mm,title='Aapoorti E-Franchise - User Experience',author='Aapoorti')
story=[]
# Cover
story += [Spacer(1,18*mm),P('AAPOORTI  /  PARTNER APP','KickerX'),P('What users see<br/>and do','TitleX'),P('A visual guide to the Aapoorti E-Franchise app, from a customer order through outlet fulfilment and delivery.','SubtitleX'),Spacer(1,4*mm)]
cover=Table([[shot('02-franchise-dashboard.png',43*mm),shot('03-franchise-order.png',43*mm),shot('09-guard-weight-check.png',43*mm)]],colWidths=[49*mm,49*mm,49*mm],hAlign='LEFT')
cover.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),4),('BOX',(0,0),(-1,-1),.5,LINE)])); story += [cover,Spacer(1,8*mm),P('One app, role-based workspaces','H2X'),P('Franchise partners place customer orders and track earnings. Outlet staff prepare bills. Guards verify custody. Delivery staff complete the final handoff. Administrators configure the network and monitor activity.','BodyX'),note('<b>Typical order journey</b> &nbsp; Franchise order &rarr; outlet bill and packing &rarr; guard verification &rarr; franchise pickup or delivery to the franchise.')]

# Access and overview
story += page_head('01  /  Getting started','Sign in to your workspace','Users sign in with the partner ID and password issued for their role. The app opens the matching workspace, with Operations, Account and sign-out controls in the header.')
story += [P('The app is designed to work in a phone browser and can be installed on a phone or tablet as a home-screen app. Live status updates refresh connected views; an offline shell and saved order drafts support limited connectivity. Final order execution and handovers need a connection.','BodyX')]
story += [P('Who uses the app','H2X'),steps([('Franchise partner','Build customer baskets, choose pickup or delivery, review commission and wallet, and follow order status.'),('Outlet POS','Bill executed orders, scan and pick products, pack each bill, and hand goods to the guard.'),('Delivery guard','Acknowledge goods, inspect seals, compare measured and expected weight, and release to the receiver.'),('Delivery staff','Accept delivery jobs, collect goods, start the trip and record delivery proof.'),('Administrator','Onboard partners, manage catalogue and outlets, configure rules, and monitor orders and dispatch.')]),Spacer(1,5),note('The app records the customer as the buyer for an order. Delivery orders are addressed to the franchise partner\'s registered delivery address; they are not customer-home deliveries.')]

# Franchise
story += page_head('02  /  Franchise partner','Place and follow an order','The franchise workspace combines earnings, sales progress, order entry and order tracking in a mobile layout.')
story += [Table([[shot('02-franchise-dashboard.png',53*mm),P('<b>Home dashboard</b><br/>The partner sees commission, purchase-wallet balance, active orders, mapped outlet, target progress and sales summaries. A business summary can be downloaded as a PDF.<br/><br/><b>Quick actions</b><br/>New order, Track orders and Dispatches open the core daily tasks.','BodyX')]],colWidths=[60*mm,103*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),6)])),Spacer(1,6)]
story += [steps([('Build a basket','Search or browse the catalogue, select pack sizes and quantities, and review the running total and commission.'),('Enter buyer details','Add the customer name and a valid 10-digit phone number.'),('Choose fulfilment','Select franchise pickup at the mapped outlet or delivery to the franchise address. Delivery price may depend on distance and current rules.'),('Review and submit','Confirm items, buyer, fulfilment, charge and required wallet balance. Execute using the purchase wallet or save the order on hold.'),('Track the order','Follow status through billing and dispatch. View the customer invoice after billing; use Dispatches for pickup/delivery progress.')]),Spacer(1,5),Table([[shot('03-franchise-order.png',40*mm),shot('04-franchise-order-created.png',40*mm)]],colWidths=[49*mm,49*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),6)]))]

# POS
story += page_head('03  /  Outlet POS','Bill, pick and pack','POS staff work from an outlet fulfilment queue. Only executed franchise orders enter billing.')
story += [Table([[shot('05-pos-home.png',52*mm),P('<b>Billing queue</b><br/>The queue shows the order, customer, destination and outlet payable. An operator claims the order for a 15-minute picking session, scans each ordered SKU and watches picked quantities update. The bill action appears only when every unit is scanned; the server checks the same rule.<br/><br/><b>Packing and dockets</b><br/>Scanned quantities cannot exceed the order. After billing, pack each bill. Docket policy can make grouping optional, automatic or required; a docket remains one dispatch and custody unit.','BodyX')]],colWidths=[60*mm,103*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),6)])),Spacer(1,6)]
story += [steps([('Claim the order','Claim it in the POS queue; the picking lease lasts 15 minutes and renews with each scan.'),('Scan and reconcile','Scan each ordered SKU and quantity. The bill button unlocks only after every ordered unit is picked.'),('Create the bill and pack','Create the bill after picking, then mark the individual parcel packed.'),('Group compatible parcels','Follow outlet policy: create a docket when required, or group packed bills when useful.'),('Handover to guard','Send the bill or docket to the guard. Print the dispatch barcode when needed.')]),Spacer(1,5),Table([[shot('06-pos-billed.png',40*mm),shot('07-pos-to-guard.png',40*mm)]],colWidths=[49*mm,49*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),6)]))]

# Guard
story += page_head('04  /  Delivery guard','Verify goods and custody','The guard desk shows bills and dockets received from POS, their status, weight readings and any alert that needs review.')
story += [Table([[shot('08-guard-home.png',48*mm),shot('09-guard-weight-check.png',48*mm)]],colWidths=[57*mm,57*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),6)])),Spacer(1,6),steps([('Acknowledge receipt','Confirm the bill or docket arriving from POS.'),('Inspect the seal','Record whether the seal is intact. A broken seal is held for review.'),('Check weight','Enter or review measured weight. The app compares it with expected weight and marks PASS or ALERT according to configured tolerance.'),('Release with the right custody','Pickup requires the partner code. Delivery assignment creates a driver task, but goods stay with the guard until the driver confirms physical pickup.'),('Keep docket custody together','A grouped docket is handled as one unit; its included bills are visible in the dispatch details.')]),Spacer(1,5),note('A weight reading is required before release. A weight alert follows the outlet blocking policy; a broken seal always blocks release. A required seal check must be recorded intact. Dockets are checked and released as one unit.')]

# Delivery
story += page_head('05  /  Delivery staff','Complete the delivery','Delivery staff see assigned tasks with franchise destination and current trip status.')
story += [Table([[shot('11-delivery-home.png',49*mm),shot('12-delivery-started.png',49*mm),shot('13-delivery-completed.png',49*mm)]],colWidths=[55*mm,55*mm,55*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),4)])),Spacer(1,6),steps([('Accept the task','Review and accept the assigned delivery.'),('Collect from guard','Confirm pickup of the bill or docket.'),('Start the trip','Open directions and proceed to the franchise address.'),('Record trip and evidence','Start live sharing for an active trip and grant location and notification permissions. The native app shares GPS in the background with a visible notification; browser sharing runs while the page stays open. The franchise dispatch screen refreshes every 10 seconds with an in-app map and route. ETA requires configured OSRM routing and has no live traffic. Geofencing and evidence rules still apply; the receiver code is required.'),('Complete or report a problem','The franchise partner generates a short-lived code after the driver is on the way. Verify that code to complete delivery. A failed trip records its last valid stage for admin review, reopening or reassignment.')]),Spacer(1,5),note('Receiver codes expire and can lock after repeated incorrect attempts. Pickup codes are generated from the partner dispatch screen after guard verification; delivery codes appear there when the driver is out for delivery. Share a code only for the matching handover.')]

# Admin and boundaries
story += page_head('06  /  Administrator','Configure and monitor the network','The desktop-oriented admin workspace manages partner access, outlets, products and operating rules, and provides visibility into live orders and dispatch.')
story += [shot('01-admin-dashboard.png',143*mm),Spacer(1,5),P('The overview places network totals, recent orders and live dispatch custody on one screen. Module tabs lead to franchisee management, catalogue, network setup and commercial rules.','SmallX')]
story += page_head('06  /  Administrator','Admin tasks and user expectations','Typical setup starts with outlets and staff, then adds franchise partners and the catalogue before the order flow is opened.')
story += [KeepTogether([steps([('Onboard partners and staff','Create franchise accounts, assign an outlet, register delivery address and manage role access.'),('Set up catalogue and network','Manage product records, outlets and operational users.'),('Configure commercial rules','Set commissions, promotions, wallet and delivery policies, fulfilment modes and docket rules.'),('Monitor exceptions','Review orders, balances, dispatch custody, weight alerts and settlement information.')]),Spacer(1,5),P('What to keep in mind','H2X'),P('The purchase wallet is debited once when an order executes. If it is short, the order stays on hold without a debit and can be executed after funding. Target rewards use delivered eligible sales; projections are estimates.','BodyX'),P('Bank payouts, provider credentials, SMS, live payment and physical device integrations depend on separate setup. This guide describes the implemented screens; exact actions and handover requirements vary by role and administrator settings.','BodyX')])]

doc.build(story,onFirstPage=footer,onLaterPages=footer)
print(OUT)
