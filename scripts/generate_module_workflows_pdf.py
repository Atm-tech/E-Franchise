"""Create the module workflow handover PDF from the maintained workflow document."""
from pathlib import Path
import re
from html import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, Flowable, KeepTogether

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'output/pdf/Aapoorti-E-Franchise-Module-Workflows.pdf'
GREEN = colors.HexColor('#0B4F3C')
INK = colors.HexColor('#19392F')
MUTED = colors.HexColor('#5E7068')
PALE = colors.HexColor('#EDF5EF')
LINE = colors.HexColor('#D6E2DA')
GOLD = colors.HexColor('#B57613')
WIDTH = A4[0] - 88

pdfmetrics.registerFont(TTFont('Workflow', 'C:/Windows/Fonts/arial.ttf'))
pdfmetrics.registerFont(TTFont('WorkflowBold', 'C:/Windows/Fonts/arialbd.ttf'))
pdfmetrics.registerFont(TTFont('WorkflowItalic', 'C:/Windows/Fonts/ariali.ttf'))
pdfmetrics.registerFontFamily('Workflow', normal='Workflow', bold='WorkflowBold', italic='WorkflowItalic', boldItalic='WorkflowBold')

styles = {
    'body': ParagraphStyle('body',fontName='Workflow',fontSize=10.2,leading=15.1,textColor=INK,spaceAfter=9),
    'step': ParagraphStyle('step',fontName='Workflow',fontSize=10.2,leading=15.1,textColor=INK,spaceAfter=9,leftIndent=22,firstLineIndent=-22),
    'small': ParagraphStyle('small',fontName='Workflow',fontSize=8.6,leading=12,textColor=MUTED,spaceAfter=7),
    'kicker': ParagraphStyle('kicker',fontName='WorkflowBold',fontSize=9,leading=12,textColor=GREEN,spaceAfter=8),
    'title': ParagraphStyle('title',fontName='WorkflowBold',fontSize=27,leading=32,textColor=GREEN,spaceAfter=12),
    'sub': ParagraphStyle('sub',fontName='WorkflowBold',fontSize=12,leading=17,textColor=GREEN,spaceBefore=10,spaceAfter=8,keepWithNext=True),
    'cell': ParagraphStyle('cell',fontName='Workflow',fontSize=9,leading=13,textColor=INK),
    'th': ParagraphStyle('th',fontName='WorkflowBold',fontSize=9,leading=12,textColor=colors.white),
}


def markup(text):
    text = text.replace('\u2011','-').replace('—','-').replace('–','-')
    text = escape(text)
    text = re.sub(r'\*\*(.*?)\*\*',r'<b>\1</b>',text)
    text = re.sub(r'`(.*?)`',r'<font color="#0B4F3C">\1</font>',text)
    text = re.sub(r'\[(.*?)\]\((.*?)\)',r'\1 (\2)',text)
    return text


def para(text,style='body'):
    return Paragraph(markup(text),styles[style])


def note(title,text):
    table=Table([[para(title,'kicker')],[para(text)]],colWidths=[WIDTH])
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),PALE),('BOX',(0,0),(-1,-1),.6,LINE),('LEFTPADDING',(0,0),(-1,-1),13),('RIGHTPADDING',(0,0),(-1,-1),13),('TOPPADDING',(0,0),(-1,0),11),('BOTTOMPADDING',(0,-1),(-1,-1),6)]))
    return table


def markdown_blocks(text):
    result=[];lines=text.strip().splitlines();i=0
    while i<len(lines):
        line=lines[i].strip()
        if not line:i+=1;continue
        if line.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].strip().startswith('|'):
                cells=[c.strip() for c in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r'[:\-]+',c) for c in cells):rows.append(cells)
                i+=1
            count=len(rows[0]);widths=([WIDTH*.30,WIDTH*.70] if count==2 else [WIDTH*.29,WIDTH*.23,WIDTH*.24,WIDTH*.24])
            table=Table([[para(c,'th' if r==0 else 'cell') for c in row] for r,row in enumerate(rows)],colWidths=widths,repeatRows=1,hAlign='LEFT')
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),GREEN),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,PALE]),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10),('LINEBELOW',(0,0),(-1,-1),.4,LINE)]))
            result.extend([table,Spacer(1,12)]);continue
        if line.startswith('### '):result.append(para(line[4:],'sub'))
        elif re.match(r'^\d+\. ',line):result.append(para(line,'step'))
        elif line.startswith('- '):result.append(para('• '+line[2:],'step'))
        else:
            while i+1<len(lines) and lines[i+1].strip() and not re.match(r'^(#|\||- |\d+\. )',lines[i+1].strip()):
                i+=1;line+=' '+lines[i].strip()
            result.append(para(line))
        i+=1
    return result


class WorkflowDiagram(Flowable):
    def __init__(self):
        super().__init__();self.width=WIDTH;self.height=435

    def draw(self):
        c=self.canv
        def box(x,y,w,h,title,description):
            c.setFillColor(PALE);c.setStrokeColor(LINE);c.roundRect(x,y,w,h,7,fill=1,stroke=1)
            p=Paragraph(title,ParagraphStyle('dt',fontName='WorkflowBold',fontSize=10,leading=12,textColor=GREEN))
            _,ph=p.wrap(w-20,h);p.drawOn(c,x+10,y+h-9-ph)
            p=Paragraph(description,ParagraphStyle('dd',fontName='Workflow',fontSize=8.7,leading=11.5,textColor=INK))
            _,ph=p.wrap(w-20,h);p.drawOn(c,x+10,y+8)
        def arrow(x1,y1,x2,y2):
            c.setStrokeColor(GREEN);c.setLineWidth(1.2);c.line(x1,y1,x2,y2)
            c.line(x2,y2,x2-3,y2+5);c.line(x2,y2,x2+3,y2+5)
        rows=[
            (380,'01  ADMIN SETUP','Outlets, staff, franchise, catalogue, rules and wallet funding'),
            (318,'02  FRANCHISE ORDER','Customer basket + fulfilment choice + sufficient wallet → EXECUTED'),
            (256,'03  POS PICKING & PACKING','Claim → scan items → bill → individual packs → optional docket'),
            (194,'04  GUARD RECEIPT & CHECK','Scan receipt → weight / count check → release only when permitted'),
        ]
        for y,title,desc in rows:box(0,y,WIDTH,49,title,desc)
        for y in [380,318,256]:arrow(WIDTH/2,y,WIDTH/2,y-13)
        c.setStrokeColor(GREEN);c.line(WIDTH/2,194,WIDTH/2,180);c.line(WIDTH*.25,180,WIDTH*.75,180)
        arrow(WIDTH*.25,180,WIDTH*.25,165);arrow(WIDTH*.75,180,WIDTH*.75,165)
        gap=15;bw=(WIDTH-gap)/2
        box(0,95,bw,70,'PICKUP PATH','Franchise generates code.<br/>Guard verifies code and releases goods.')
        box(bw+gap,95,bw,70,'DELIVERY PATH','Accept → pickup → trip.<br/>Receiver code + proof / GPS if enabled.')
        c.line(WIDTH*.25,95,WIDTH*.25,79);c.line(WIDTH*.75,95,WIDTH*.75,79);c.line(WIDTH*.25,79,WIDTH*.75,79)
        arrow(WIDTH/2,79,WIDTH/2,64)
        box(0,3,WIDTH,61,'05  COMPLETION & FINANCE','DELIVERED → monthly reward evaluation → settlement eligibility.<br/>Admin batches payable orders and records external bank payment.')


class WorkflowPDF(SimpleDocTemplate):
    def afterFlowable(self,flowable):
        if isinstance(flowable,Paragraph) and flowable.style.name=='title':
            key='module-'+str(self.page)
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(flowable.getPlainText(),key,level=0,closed=False)


def furniture(canvas,doc):
    w,h=A4
    canvas.setFillColor(GREEN);canvas.rect(0,h-9,w,9,fill=1,stroke=0)
    canvas.setFont('WorkflowBold',9);canvas.drawString(44,h-34,'AAPOORTI  /  E-FRANCHISE')
    canvas.setFillColor(MUTED);canvas.setFont('Workflow',8);canvas.drawRightString(w-44,h-34,'MODULE WORKFLOWS  •  17 SEP 2026')
    canvas.setStrokeColor(LINE);canvas.line(44,43,w-44,43)
    canvas.setFont('Workflow',8);canvas.drawString(44,28,'Operational handover | Based on the maintained workflow specification')
    canvas.drawRightString(w-44,28,f'{doc.page:02d}')


def build():
    source=(ROOT/'docs/MODULE-WORKFLOWS.md').read_text(encoding='utf-8')
    sections={}
    for chunk in re.split(r'(?m)^## ',source)[1:]:
        heading,body=chunk.split('\n',1);sections[heading.split('.')[0]]=body.strip()
    franchise=sections['3']
    ordering,finance=franchise.split('### Manual wallet flow',1)
    pages=[
        ('01 / MASTER FLOW','From order to completion',None),
        ('02 / ADMIN','Setup and control',sections['2']),
        ('03 / FRANCHISE','Ordering and wallet pricing',ordering),
        ('04 / WALLETS & PAYMENTS','Funding and reconciliation','### Manual wallet flow'+finance),
        ('05 / OUTLET POS','Pick, bill and pack',sections['4']),
        ('06 / DELIVERY GUARD','Receive, check and release',sections['5']),
        ('07 / DELIVERY TEAM','Pickup to verified delivery',sections['6']),
        ('08 / FINANCE','Settlement and reporting',sections['7']),
        ('09 / SHARED CAPABILITIES','Mobile, offline and security',sections['8']),
        ('10 / HANDOVER STATUS','Ready features and activation',sections['9'].split('Technical setup:')[0]),
    ]
    story=[]
    for number,(kicker,title,body) in enumerate(pages):
        if number:story.append(PageBreak())
        story.extend([para(kicker,'kicker'),para(title,'title')])
        if body is None:
            story.append(para('Har role ke liye alag dashboard aur Operations Work Desk. Yeh guide daily steps, handovers aur exception handling ko ek jagah explain karti hai.'))
            story.extend([WorkflowDiagram(),Spacer(1,12)])
            story.append(note('WHEN THE FLOW PAUSES','Low balance / save for later → ON HOLD → fund wallet → execute. Blocking weight/count mismatch → recheck → verify again. Failed delivery → admin review / reopen / reassign.'))
            story.extend([Spacer(1,12),para('Inside: Admin 02 · Franchise 03 · Wallets 04 · POS 05 · Guard 06 · Delivery 07 · Settlement 08 · Mobile 09 · Status 10','small')])
        else:story.extend(markdown_blocks(body))
        if number==7:
            story.extend([Spacer(1,15),note('SETTLEMENT BOUNDARY','PAID records a completed external bank payment. The application does not initiate a bank transfer. Payment reference aur exact amount verify karke hi status update karein.')])
        if number==9:
            story.extend([Spacer(1,12),para('Source: docs/MODULE-WORKFLOWS.md. Configuration: docs/DEPLOYMENT.md. Verification evidence: docs/VERIFICATION.md.','small'),note('HOW TO USE THIS GUIDE','Training ke liye relevant module page share karein. Live rollout se pehle provider credentials, hardware commissioning, physical-device checks aur hosting activation complete karein. This PDF describes current workflows; it is not a production-readiness certification.')])
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    doc=WorkflowPDF(str(OUTPUT),pagesize=A4,rightMargin=44,leftMargin=44,topMargin=62,bottomMargin=60,title='Aapoorti E-Franchise - Module Workflows',author='Aapoorti',subject='Module-wise operations and handover guide')
    doc.build(story,onFirstPage=furniture,onLaterPages=furniture)
    print(OUTPUT)


if __name__=='__main__':build()
