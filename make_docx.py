import os
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

SRC_IMG = r"C:\Users\Laptop\Downloads\WhatsApp Image 2026-10-15 at 12.27.10 PM.jpg"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kararnama_urdu.docx")

URDU_FONT = "Urdu Typesetting"
LATIN_FONT = "Times New Roman"


def set_bidi(p):
    pPr = p._p.get_or_add_pPr()
    if hasattr(pPr, "get_or_add_bidi"):
        pPr.get_or_add_bidi()
        return
    before = ["w:pStyle", "w:keepNext", "w:keepLines", "w:pageBreakBefore", "w:framePr",
              "w:widowControl", "w:numPr", "w:suppressLineNumbers", "w:pBdr", "w:shd",
              "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap",
              "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN",
              "w:bidi", "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind",
              "w:contextualSpacing", "w:mirrorIndents", "w:suppressOverlap", "w:jc"]
    tags = [qn(t) for t in before]
    idx = 0
    for i, child in enumerate(list(pPr)):
        if child.tag in tags:
            idx = i + 1
    pPr.insert(idx, OxmlElement("w:bidi"))


def style_run(run, size=14, bold=False, color=None):
    run.font.name = LATIN_FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rPr = run._r.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn("w:ascii"), LATIN_FONT)
    rFonts.set(qn("w:hAnsi"), LATIN_FONT)
    rFonts.set(qn("w:cs"), URDU_FONT)
    if hasattr(rPr, "get_or_add_rtl"):
        rPr.get_or_add_rtl()
    else:
        rPr.append(OxmlElement("w:rtl"))


def add_para(doc, text, size=14, align=WD_ALIGN_PARAGRAPH.RIGHT, bold=False,
             color=None, space_after=8, space_before=0):
    p = doc.add_paragraph()
    p.alignment = align
    set_bidi(p)
    pf = p.paragraph_format
    pf.space_after = Pt(space_after)
    pf.space_before = Pt(space_before)
    pf.line_spacing = 1.2
    r = p.add_run(text)
    style_run(r, size=size, bold=bold, color=color)
    return p


def no_borders(table):
    tblPr = table._tbl.tblPr
    if hasattr(tblPr, "get_or_add_tblBorders"):
        borders = tblPr.get_or_add_tblBorders()
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            e = borders.find(qn("w:" + edge))
            if e is None:
                e = OxmlElement("w:" + edge)
                borders.append(e)
            e.set(qn("w:val"), "none")
            e.set(qn("w:sz"), "0")
        return
    after = ["w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption",
             "w:tblDescription", "w:tblPrChange"]
    tags = [qn(t) for t in after]
    idx = len(tblPr)
    for i, child in enumerate(list(tblPr)):
        if child.tag in tags:
            idx = i
            break
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement("w:" + edge)
        e.set(qn("w:val"), "none")
        e.set(qn("w:sz"), "0")
        borders.append(e)
    tblPr.insert(idx, borders)


doc = Document()

style = doc.styles["Normal"]
style.font.name = URDU_FONT
style.font.size = Pt(14)
rPr = style.element.get_or_add_rPr()
rFonts = rPr.get_or_add_rFonts()
rFonts.set(qn("w:ascii"), LATIN_FONT)
rFonts.set(qn("w:hAnsi"), LATIN_FONT)
rFonts.set(qn("w:cs"), URDU_FONT)

section = doc.sections[0]
section.left_margin = Inches(0.8)
section.right_margin = Inches(0.8)
section.top_margin = Inches(0.7)
section.bottom_margin = Inches(0.7)

# Title
add_para(doc, "کراہڑنامہ", size=20, align=WD_ALIGN_PARAGRAPH.CENTER, bold=True, space_after=6)

# Disclaimer notes
add_para(doc, "نوٹ: یہ دستاویز تصویر سے خودکار (OCR) طریقے سے تحریر کی گئی ہے۔ "
              "چونکہ اصل تصویر کم وضاحت (low resolution) ہے، کچھ الفاظ اور نمبر "
              "غلطیا غیر فہم ہو سکتے ہیں۔",
         size=11, color=RGBColor(0x66, 0x66, 0x66), space_after=2)
add_para(doc, "Note: This text was transcribed automatically (OCR) from the image. "
              "As the source scan is low-resolution, some words and numbers may be "
              "misread or garbled.",
         size=10, align=WD_ALIGN_PARAGRAPH.LEFT, color=RGBColor(0x66, 0x66, 0x66),
         space_after=10)

# Original image
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run()
run.add_picture(SRC_IMG, width=Inches(6.0))
p.paragraph_format.space_after = Pt(12)

add_para(doc, "تحریری متن (OCR)", size=16, align=WD_ALIGN_PARAGRAPH.CENTER,
         bold=True, space_before=6, space_after=10)

INTRO = [
    "میں کمیں ہسٹنگہیبرڈ رولڈنگ واغیرہ اٹیلی ساکن، مکان نمبر C-50، الیونچ مہری "
    "سریٹ، واہاں کینٹ کنشیل بیسلا راولپنڈی کا ہوں جو کر حالیہ بیاں کرتا ہوں ۔",
    "میں کمیں ہسٹنگہیبرڈ رولڈنگ (کراہڑنامہ) آپ ہیچا پیچھا پھلور لفہ شاہراجہ ساکن، "
    "مکان نمبر CB-A-136/1، گلی نمبر 07، ساہا والا کالونی واغیرہ کینٹ کنشیل بیسلا "
    "شاہاں ریلویشن راولپنڈی (ما کا مکان)",
    "ایک عہد نامہ نمبر D-1042، لین نمبر 20، (گرینڈ فلور) الیونچ کالونی واغیرہ کینٹ "
    "کنشیل بیسلا شاہاں ریلویشن، راولپنڈی 01-09-2026 سے مندرجہ ذیل شرح کاٹ پر اجرا "
    "کیا گیا ہے ۔",
]
for t in INTRO:
    add_para(doc, t)

CLAUSES = [
    ("۱۔ یہ کہ ہمیں نے مکان منتقل کر لیا ہے مالک کا مقارنہ نشئیہ چھڑھ روپے ہر ماہ "
     "منفرد (RS.29,000/-) کے پہلے بجکر برماہی کیمرہ سے 05 ماہ تک بجک اور کرنے کا "
     "پابند ہوگا ۔", None),
    ("۲۔ یہ کہ ہمیں نے مکان منتقل کروا لیا ہے مالک کی با بیٹھ ساٹھ ہزار روپے "
     "منفرد (RS.60,000/-) سے رولے ایسٹری کیمرہ والا ایک مکان کروا دیا کر دیا ہے جو "
     "کہ مکان چھوڑنے پ",
     "ویسے ہے کے پہلا جات کی اب اسٹی کے بعد (بیغریم) واجب لینے کی تخلص با رہول گا ۔"),
    ("۳۔ یہ کہ مکان خانی کرنے یا کرانے کی صورت میں وڈوں دار میٹین (02) ماہ ٹکل "
     "نوٹس دینے گے اور ڈوں دار سلے پر کراہڑ وار میںر صورت باہش وحجزت مکان خانی "
     "کرنے کا پابند ہو", None),
    ("۴۔ یہ کہ مکان کا فیڈن لینے کرنے کی صورت میں کراہاں وار ڈویلڈا کرنے کا", None),
    ("۵۔ یہ کہ کراہاں میں باضافہ ایک سال بعد ظہیر دس فیصد (10)% ہوگا ۔", None),
    ("۶۔ یہ کہ مکان منتقل کر دیا کے بعد نظام شناخت اسی مشکل کا سچکا علا مان مزید نک "
     "کر کھولن کے کھٹوں کی خاوند وبیغر وہ کے نصب کرلیا کے بعد حسب یا اور کے نسبت "
     "ایک مثال کے میں با پر بولن کے کھولن", None),
    ("۷۔ یہ کہ مکان منتقل کر دیا ہے اگر میں کسی خلاف نمون کا مہم کروں گا ہر اس "
     "کرنا کی خر اس کمان کی مٹی حرا لت سے حکم استنا ہی (Stay) لینے کا امیر تور پیا "
     "از نہوں گا ۔", None),
    ("۸۔ یہ کہ مکان منتقل کر دیا کا فیصلہ حسب از نمونہ یا لک کے کی اور کے بقول پر "
     "کرکھولنے کا کھازنہ بیگھڑنے کا کھاز نہوں گا اور مکان میں کسی مککی تہیل یہا باز "
     "شے اک کہ کیس کہ مسیری جازت کے بیگھڑنے کے لی", None),
    ("۹۔ یہ کہ مکان مالک کے ساتھ خانی کروں گا ۔ اور چاہیں مالک کے عطا ہے چاہے کروں "
     "گا ۔", None),
    ("۱۰۔ یہ کہ معاہدہ ونیزی ہمی ری مٹ رفعہ ایک (01) سال کے لیے کے جیگند یہ معاہدہ "
     "ونیزی ہمی ری رفع منڈی سے لے کیا جا ئے گا ۔", None),
]

for main, cont in CLAUSES:
    add_para(doc, main)
    if cont:
        add_para(doc, cont, space_after=8)

add_para(doc, "لبندیاں ٹھیں خوشی ہوا چاہی ہوں چواس خمس بلا نچے روا کا مال روپے ہوگا "
              "باہاں فرے کرنا یا ہے نتا گے سندب ہے",
         align=WD_ALIGN_PARAGRAPH.CENTER, space_before=6, space_after=18)

# Signature block
table = doc.add_table(rows=1, cols=2)
table.autofit = True
no_borders(table)

right_cell, left_cell = table.rows[0].cells


def fill_cell(cell, lines, align):
    first = True
    for text, bold, size in lines:
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        p.alignment = align
        set_bidi(p)
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run(text)
        style_run(r, size=size, bold=bold)


fill_cell(right_cell, [
    ("المیر (کراہڑزار)", True, 13),
    ("حسین جمیل", True, 13),
    ("(37406-8567622-7)", False, 12),
], WD_ALIGN_PARAGRAPH.RIGHT)

fill_cell(left_cell, [
    ("المیر (مالک مکان)", True, 13),
    ("محمد فیصل رمضان", True, 13),
    ("(37406-1579383-5)", False, 12),
], WD_ALIGN_PARAGRAPH.RIGHT)

doc.save(OUT)
print("saved:", OUT)

# verify
d2 = Document(OUT)
print("paragraphs:", len(d2.paragraphs), "tables:", len(d2.tables))
