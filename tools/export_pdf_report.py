#!/usr/bin/env python3
"""
Export the social media audit as a professionally branded PDF report.
Brand: ABGrowthCo — black, white, gray palette with logo placeholder.

To use your actual logo: place a PNG/JPG at assets/logo.png in the project
root. The tool will embed it automatically; if not found it renders a
placeholder box so the layout is always correct.

Usage:
  python tools/export_pdf_report.py \
    --business "Gino's New York Pizza" \
    --location "Buffalo, NY" \
    --reviews-file  .tmp/google_reviews.json \
    --ads-file      .tmp/meta_ads.json \
    --competitors-file .tmp/competitors.json \
    --social-file   .tmp/social_presence.json \
    --report-file   .tmp/audit_ginos_new_york_pizza_buffalo_ny.md \
    --output        .tmp/audit_ginos_new_york_pizza_buffalo_ny.pdf
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import Flowable

# ─────────────────────────────────────────────────────────────────────────────
# BRAND PALETTE  —  Black · White · Gray
# ─────────────────────────────────────────────────────────────────────────────
BLACK      = colors.HexColor("#111111")   # primary — headings, headers
CHARCOAL   = colors.HexColor("#2D2D2D")   # secondary text, labels
MID_GRAY   = colors.HexColor("#6B7280")   # body text, captions
LIGHT_GRAY = colors.HexColor("#F4F4F4")   # card / table alt-row background
RULE_GRAY  = colors.HexColor("#D1D5DB")   # dividers, table borders
DARK_GRAY  = colors.HexColor("#3F3F3F")   # section header background
WHITE      = colors.HexColor("#FFFFFF")

# Functional status colours (data-only, not brand)
STATUS_GREEN  = colors.HexColor("#16A34A")
STATUS_ORANGE = colors.HexColor("#D97706")
STATUS_RED    = colors.HexColor("#DC2626")

PAGE_W, PAGE_H = letter
MARGIN = 0.65 * inch
USABLE_W = PAGE_W - 2 * MARGIN

# Logo file location (relative to project root)
LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "logo.png")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def load_json(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def strip_md(text):
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*",     r"\1", text)
    return text.strip()


def na(val, suffix=""):
    return "N/A" if (val is None or val == "") else f"{val}{suffix}"


def _wrap_words(text, max_chars):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= max_chars:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [text]


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOM FLOWABLES
# ─────────────────────────────────────────────────────────────────────────────

class LogoPlaceholder(Flowable):
    """
    Renders your logo if assets/logo.png exists.
    Otherwise draws a dashed placeholder box labelled 'INSERT LOGO'.
    width/height define the bounding box; the image scales to fit.
    """
    def __init__(self, width, height, on_dark=False):
        super().__init__()
        self.width   = width
        self.height  = height
        self.on_dark = on_dark   # True when sitting on a dark background

    def draw(self):
        c = self.canv
        if os.path.exists(LOGO_PATH):
            from reportlab.lib.utils import ImageReader
            img = ImageReader(LOGO_PATH)
            iw, ih = img.getSize()
            scale = min(self.width / iw, self.height / ih)
            dw, dh = iw * scale, ih * scale
            x = (self.width  - dw) / 2
            y = (self.height - dh) / 2
            c.drawImage(LOGO_PATH, x, y, dw, dh, mask="auto")
        else:
            # Dashed placeholder box
            border = WHITE if self.on_dark else RULE_GRAY
            c.setStrokeColor(border)
            c.setLineWidth(0.8)
            c.setDash(3, 3)
            c.roundRect(0, 0, self.width, self.height, 4, stroke=1, fill=0)
            c.setDash()   # reset
            # Cross-lines inside
            c.setLineWidth(0.4)
            c.line(0, 0, self.width, self.height)
            c.line(self.width, 0, 0, self.height)
            # Label
            label_color = WHITE if self.on_dark else MID_GRAY
            c.setFillColor(label_color)
            c.setFont("Helvetica-Bold", 7)
            c.drawCentredString(self.width / 2, self.height / 2 + 4,  "YOUR LOGO HERE")
            c.setFont("Helvetica", 6)
            c.drawCentredString(self.width / 2, self.height / 2 - 6, "place logo.png in assets/")


class SectionHeader(Flowable):
    """Dark pill banner — section number badge + title."""
    def __init__(self, number, title, width):
        super().__init__()
        self.number = number
        self.title  = title
        self.width  = width
        self.height = 28

    def draw(self):
        c = self.canv
        # Dark background bar
        c.setFillColor(DARK_GRAY)
        c.roundRect(0, 0, self.width, self.height, 4, stroke=0, fill=1)
        # Number badge (white filled)
        badge_w, badge_h = 22, 18
        bx, by = 8, 5
        c.setFillColor(WHITE)
        c.roundRect(bx, by, badge_w, badge_h, 3, stroke=0, fill=1)
        c.setFillColor(BLACK)
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(bx + badge_w / 2, by + 4, str(self.number))
        # Title text
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(WHITE)
        c.drawString(38, 8, self.title.upper())
        # Thin white rule at bottom of badge
        c.setStrokeColor(colors.HexColor("#555555"))
        c.setLineWidth(0.3)
        c.line(8, 4, self.width - 8, 4)


class MetricCard(Flowable):
    """Stat card: small label above, large value below."""
    def __init__(self, label, value, width, height=54,
                 value_color=None, alert=False):
        super().__init__()
        self.label       = label
        self.value       = value
        self.width       = width
        self.height      = height
        self.value_color = value_color or BLACK
        self.alert       = alert   # draws top border in status colour

    def draw(self):
        c = self.canv
        # Card background
        c.setFillColor(LIGHT_GRAY)
        c.roundRect(0, 0, self.width, self.height, 4, stroke=0, fill=1)
        # Thin top border
        top_color = self.value_color if self.alert else RULE_GRAY
        c.setStrokeColor(top_color)
        c.setLineWidth(2.5 if self.alert else 0.5)
        c.line(0, self.height, self.width, self.height)
        # Label
        c.setFont("Helvetica", 7.5)
        c.setFillColor(MID_GRAY)
        c.drawString(10, self.height - 15, self.label.upper())
        # Value
        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(self.value_color)
        c.drawString(10, 13, str(self.value))


# ─────────────────────────────────────────────────────────────────────────────
# PAGE CALLBACKS
# ─────────────────────────────────────────────────────────────────────────────

def make_cover_canvas(canvas, doc, business, location, report_date):
    canvas.saveState()
    W, H = PAGE_W, PAGE_H

    # ── Full white background ─────────────────────────────────────────────
    canvas.setFillColor(WHITE)
    canvas.rect(0, 0, W, H, stroke=0, fill=1)

    # ── Black top panel (top 44%) ─────────────────────────────────────────
    panel_h = H * 0.44
    canvas.setFillColor(BLACK)
    canvas.rect(0, H - panel_h, W, panel_h, stroke=0, fill=1)

    # ── Thin horizontal accent stripe between panels ──────────────────────
    canvas.setFillColor(CHARCOAL)
    canvas.rect(0, H - panel_h - 3, W, 3, stroke=0, fill=1)

    # ── Logo placeholder — top-left of black panel ────────────────────────
    logo_w, logo_h = 130, 44
    lx = MARGIN
    ly = H - MARGIN - logo_h - 6
    canvas.saveState()
    canvas.translate(lx, ly)
    lp = LogoPlaceholder(logo_w, logo_h, on_dark=True)
    lp.canv = canvas
    lp.draw()
    canvas.restoreState()

    # ── Tagline next to logo ──────────────────────────────────────────────
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#9CA3AF"))
    canvas.drawString(lx + logo_w + 12, ly + logo_h - 12, "ABGrowthCo")
    canvas.drawString(lx + logo_w + 12, ly + logo_h - 24, "Digital Growth Agency")

    # ── Thin horizontal rule below logo row ──────────────────────────────
    canvas.setStrokeColor(colors.HexColor("#333333"))
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, H - MARGIN - logo_h - 16, W - MARGIN, H - MARGIN - logo_h - 16)

    # ── Report type label ─────────────────────────────────────────────────
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#9CA3AF"))
    canvas.drawString(MARGIN, H - panel_h + 42, "SOCIAL MEDIA MARKETING AUDIT REPORT")

    # ── Business name — large, in white ──────────────────────────────────
    canvas.setFont("Helvetica-Bold", 32)
    canvas.setFillColor(WHITE)
    name_lines = _wrap_words(business, 26)
    ny = H - panel_h + 28 + len(name_lines) * 36
    for line in name_lines:
        canvas.drawString(MARGIN, ny, line)
        ny -= 36

    # ── Location ──────────────────────────────────────────────────────────
    canvas.setFont("Helvetica", 13)
    canvas.setFillColor(colors.HexColor("#D1D5DB"))
    canvas.drawString(MARGIN, H - panel_h + 24, location)

    # ── White lower panel — meta info ─────────────────────────────────────
    info_y = H - panel_h - 52
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(CHARCOAL)
    canvas.drawString(MARGIN, info_y,      "REPORT DATE")
    canvas.drawString(MARGIN + 200, info_y, "PREPARED BY")
    canvas.drawString(MARGIN + 380, info_y, "CLASSIFICATION")

    canvas.setFont("Helvetica", 10)
    canvas.setFillColor(BLACK)
    canvas.drawString(MARGIN,       info_y - 14, report_date)
    canvas.drawString(MARGIN + 200, info_y - 14, "ABGrowthCo")
    canvas.drawString(MARGIN + 380, info_y - 14, "Confidential")

    # Thin rule below meta
    canvas.setStrokeColor(RULE_GRAY)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, info_y - 26, W - MARGIN, info_y - 26)

    # ── Decorative vertical stripe on right edge ──────────────────────────
    canvas.setFillColor(DARK_GRAY)
    canvas.rect(W - 8, 0, 8, H, stroke=0, fill=1)

    # ── Bottom bar ────────────────────────────────────────────────────────
    canvas.setFillColor(BLACK)
    canvas.rect(0, 0, W, 36, stroke=0, fill=1)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#9CA3AF"))
    canvas.drawString(MARGIN, 13, "ABGrowthCo  ·  Digital Growth Agency")
    canvas.drawRightString(W - 20, 13, "abgrowthco.com")

    canvas.restoreState()


def make_inner_canvas(canvas, doc):
    canvas.saveState()
    W, H = PAGE_W, PAGE_H

    # ── Top header bar — black ────────────────────────────────────────────
    canvas.setFillColor(BLACK)
    canvas.rect(0, H - 34, W, 34, stroke=0, fill=1)

    # Logo placeholder — small, in header
    logo_w, logo_h = 60, 20
    canvas.saveState()
    canvas.translate(MARGIN, H - 34 + (34 - logo_h) / 2)
    lp = LogoPlaceholder(logo_w, logo_h, on_dark=True)
    lp.canv = canvas
    lp.draw()
    canvas.restoreState()

    # Report label
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#9CA3AF"))
    canvas.drawString(MARGIN + logo_w + 10, H - 20, "SOCIAL MEDIA MARKETING AUDIT REPORT")

    # Page number
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(WHITE)
    canvas.drawRightString(W - MARGIN, H - 21, f"PAGE {doc.page}")

    # ── Footer ────────────────────────────────────────────────────────────
    canvas.setStrokeColor(RULE_GRAY)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, 30, W - MARGIN, 30)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MID_GRAY)
    canvas.drawString(MARGIN, 16, "ABGrowthCo  ·  Confidential — Client Prospect")
    canvas.drawRightString(W - MARGIN, 16, "abgrowthco.com")

    # Right accent stripe
    canvas.setFillColor(DARK_GRAY)
    canvas.rect(W - 6, 0, 6, H, stroke=0, fill=1)

    canvas.restoreState()


# ─────────────────────────────────────────────────────────────────────────────
# STYLES
# ─────────────────────────────────────────────────────────────────────────────

def build_styles():
    s = {}

    s["h2"] = ParagraphStyle("h2",
        fontName="Helvetica-Bold", fontSize=12, textColor=BLACK,
        spaceAfter=4, spaceBefore=8)

    s["body"] = ParagraphStyle("body",
        fontName="Helvetica", fontSize=9.5, textColor=CHARCOAL,
        leading=14.5, spaceAfter=5)

    s["bullet_title"] = ParagraphStyle("bullet_title",
        fontName="Helvetica-Bold", fontSize=9.5, textColor=BLACK,
        leading=14, spaceAfter=1, leftIndent=12, firstLineIndent=-12)

    s["bullet"] = ParagraphStyle("bullet",
        fontName="Helvetica", fontSize=9.5, textColor=CHARCOAL,
        leading=14, spaceAfter=5, leftIndent=12, firstLineIndent=-12)

    s["label"] = ParagraphStyle("label",
        fontName="Helvetica-Bold", fontSize=7.5, textColor=MID_GRAY,
        spaceAfter=2)

    s["caption"] = ParagraphStyle("caption",
        fontName="Helvetica", fontSize=7.5, textColor=MID_GRAY,
        spaceAfter=2)

    s["th"] = ParagraphStyle("th",
        fontName="Helvetica-Bold", fontSize=8.5, textColor=WHITE,
        alignment=TA_CENTER)

    s["td"] = ParagraphStyle("td",
        fontName="Helvetica", fontSize=8.5, textColor=CHARCOAL,
        alignment=TA_CENTER, leading=12)

    s["td_left"] = ParagraphStyle("td_left",
        fontName="Helvetica", fontSize=8.5, textColor=CHARCOAL,
        alignment=TA_LEFT, leading=12)

    s["td_bold"] = ParagraphStyle("td_bold",
        fontName="Helvetica-Bold", fontSize=8.5, textColor=BLACK,
        alignment=TA_LEFT, leading=12)

    return s


# ─────────────────────────────────────────────────────────────────────────────
# CONTENT PARSERS
# ─────────────────────────────────────────────────────────────────────────────

def parse_bullets(text, styles):
    out = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        line = line.lstrip("-•").strip()
        m = re.match(r"\*\*(.*?)\*\*(.*)", line)
        if m:
            title = m.group(1).strip(" :")
            body  = m.group(2).strip(" :—-")
            out.append(Paragraph(f"▸  {title}", styles["bullet_title"]))
            if body:
                out.append(Paragraph(strip_md(body), styles["bullet"]))
        else:
            out.append(Paragraph(f"▸  {strip_md(line)}", styles["bullet"]))
    return out


def parse_paragraphs(text, styles):
    out = []
    for block in re.split(r"\n{2,}", text.strip()):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.lstrip().startswith(("- ", "• ", "- **", "• **")):
                out += parse_bullets(line, styles)
            else:
                out.append(Paragraph(strip_md(line), styles["body"]))
        out.append(Spacer(1, 4))
    return out


def extract_section(md, heading):
    m = re.search(
        rf"##\s+\d+\.\s+{re.escape(heading)}\n(.*?)(?=\n##|\Z)",
        md, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m2 = re.search(
        rf"##.*?{re.escape(heading)}.*?\n(.*?)(?=\n##|\Z)",
        md, re.DOTALL | re.IGNORECASE)
    return m2.group(1).strip() if m2 else ""


# ─────────────────────────────────────────────────────────────────────────────
# SECTION BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def section(number, title, body_flowables, styles):
    out = [
        Spacer(1, 10),
        SectionHeader(number, title, USABLE_W),
        Spacer(1, 10),
        *body_flowables,
        Spacer(1, 6),
        HRFlowable(width=USABLE_W, thickness=0.4, color=RULE_GRAY),
    ]
    return out


# ─────────────────────────────────────────────────────────────────────────────
# PDF BUILD
# ─────────────────────────────────────────────────────────────────────────────

def build_pdf(business, location, reviews, ads, competitors, social,
              report_md, output_path):
    styles  = build_styles()
    today   = datetime.now(timezone.utc).strftime("%B %d, %Y")

    # ── Data shortcuts ────────────────────────────────────────────────────
    rating    = reviews.get("rating")
    rev_count = reviews.get("review_count")
    biz_type  = reviews.get("primary_type", "Business").title()
    ad_count  = ads.get("active_ad_count", 0)
    ad_assess = ads.get("assessment", "unknown").replace("_", " ").title()
    fb        = social.get("facebook", {})
    ig        = social.get("instagram", {})
    soc_score = social.get("overall_social_score", 1)
    comp_list = competitors.get("competitors", [])

    # ── Extract MD narrative sections ─────────────────────────────────────
    overview_txt = extract_section(report_md, "Business Overview")
    wrong_txt    = extract_section(report_md, "What They Are Doing Wrong")
    comp_txt     = extract_section(report_md, "What Competitors Are Doing Better")
    opps_txt     = extract_section(report_md, "Opportunities ABGrowthCo Can Exploit")
    ads_txt      = extract_section(report_md, "Recommended Ad Strategy")

    # Strip embedded competitor table from comp_txt (rebuilt below)
    comp_txt = re.sub(r"\|.*\|.*\n", "", comp_txt)
    comp_txt = re.sub(r"\n{3,}", "\n\n", comp_txt).strip()

    # ── Doc setup ─────────────────────────────────────────────────────────
    story = []

    class AuditDoc(BaseDocTemplate):
        def __init__(self, filename, **kw):
            super().__init__(filename, **kw)
            cover_frame = Frame(0, 0, PAGE_W, PAGE_H,
                                leftPadding=0, rightPadding=0,
                                topPadding=0, bottomPadding=0)
            inner_frame = Frame(MARGIN, 42, USABLE_W, PAGE_H - 42 - 42,
                                leftPadding=0, rightPadding=0,
                                topPadding=4, bottomPadding=0)
            self.addPageTemplates([
                PageTemplate(id="Cover", frames=[cover_frame],
                             onPage=lambda c, d: make_cover_canvas(
                                 c, d, business, location, today)),
                PageTemplate(id="Inner", frames=[inner_frame],
                             onPage=make_inner_canvas),
            ])

    doc = AuditDoc(output_path, pagesize=letter,
                   title=f"Social Media Audit — {business}",
                   author="ABGrowthCo")

    # Cover page is drawn entirely by onPage; just flip to Inner for content
    story.append(NextPageTemplate("Inner"))
    story.append(PageBreak())

    # ═════════════════════════════════════════════════════════════════════
    # SECTION 1 — BUSINESS OVERVIEW
    # ═════════════════════════════════════════════════════════════════════
    card_w = (USABLE_W - 12) / 4

    def _ad_color():
        if ad_count == 0:         return STATUS_RED,    True
        if ad_count and ad_count <= 2: return STATUS_ORANGE, True
        return STATUS_GREEN, True

    def _soc_color():
        if soc_score <= 2: return STATUS_RED,    True
        if soc_score <= 3: return STATUS_ORANGE, True
        return STATUS_GREEN, True

    ad_col,  ad_alert  = _ad_color()
    soc_col, soc_alert = _soc_color()
    rat_col  = STATUS_GREEN if (rating and float(rating) >= 4.0) else STATUS_ORANGE
    fb_q     = fb.get("presence_quality", "no presence").replace("_", " ").title()
    fb_col   = STATUS_RED if any(x in fb_q.lower() for x in ("no", "ghost")) else CHARCOAL

    row1 = [
        MetricCard("Google Rating",   f"{na(rating)}★",  card_w, value_color=rat_col, alert=True),
        MetricCard("Total Reviews",   na(rev_count),      card_w),
        MetricCard("Active Meta Ads", str(ad_count or 0), card_w, value_color=ad_col, alert=ad_alert),
        MetricCard("Social Score",    f"{soc_score} / 5", card_w, value_color=soc_col, alert=soc_alert),
    ]
    fb_val = na(fb.get("follower_count")) if fb.get("found") else "Not Found"
    ig_val = f"@{ig['username']}" if ig.get("found") else "Not Found"
    row2 = [
        MetricCard("Business Type",   biz_type[:18],     card_w),
        MetricCard("Facebook Status", fb_q,              card_w, value_color=fb_col, alert=fb_col==STATUS_RED),
        MetricCard("FB Followers",    fb_val,            card_w),
        MetricCard("Instagram",       ig_val,            card_w,
                   value_color=STATUS_RED if not ig.get("found") else CHARCOAL,
                   alert=not ig.get("found")),
    ]

    def cards_table(cards):
        t = Table([cards], colWidths=[card_w]*4, rowHeights=[56])
        t.setStyle(TableStyle([
            ("LEFTPADDING",  (0,0),(-1,-1), 3),
            ("RIGHTPADDING", (0,0),(-1,-1), 3),
            ("TOPPADDING",   (0,0),(-1,-1), 0),
            ("BOTTOMPADDING",(0,0),(-1,-1), 0),
        ]))
        return t

    ov = []
    ov.append(Paragraph("AT A GLANCE", styles["label"]))
    ov.append(Spacer(1, 5))
    ov.append(cards_table(row1))
    ov.append(Spacer(1, 6))
    ov.append(cards_table(row2))
    ov.append(Spacer(1, 10))
    if overview_txt and "[Narrative" not in overview_txt:
        ov += parse_paragraphs(overview_txt, styles)
    story += section(1, "Business Overview", ov, styles)

    # ═════════════════════════════════════════════════════════════════════
    # SECTION 2 — WHAT THEY ARE DOING WRONG
    # ═════════════════════════════════════════════════════════════════════
    wrong = parse_bullets(wrong_txt, styles) if (wrong_txt and "[Narrative" not in wrong_txt) \
            else [Paragraph("No data available.", styles["body"])]
    story += section(2, "What They Are Doing Wrong", wrong, styles)

    # ═════════════════════════════════════════════════════════════════════
    # SECTION 3 — COMPETITOR ANALYSIS
    # ═════════════════════════════════════════════════════════════════════
    cw = [USABLE_W*0.34, USABLE_W*0.14, USABLE_W*0.17, USABLE_W*0.18, USABLE_W*0.17]
    thead = [[Paragraph(h, styles["th"]) for h in
              ("Business", "Rating", "Reviews", "Meta Ads", "Status")]]
    # Audited row
    tdata = thead + [[
        Paragraph(f"★  {business}", styles["td_bold"]),
        Paragraph(f"{na(rating)}★",  styles["td"]),
        Paragraph(str(na(rev_count)), styles["td"]),
        Paragraph(ad_assess,          styles["td"]),
        Paragraph("AUDITED",          styles["td"]),
    ]]
    for c in comp_list:
        c_assess = c.get("meta_ad_assessment","unknown").replace("_"," ").title()
        c_ads    = c.get("active_meta_ads")
        c_status = ("✓ Active" if c_ads and c_ads > 0
                    else "No Ads" if c_ads == 0 else "Unknown")
        tdata.append([
            Paragraph(c.get("name","—"),     styles["td_left"]),
            Paragraph(f"{na(c.get('rating'))}★", styles["td"]),
            Paragraph(str(na(c.get("review_count"))), styles["td"]),
            Paragraph(c_assess,               styles["td"]),
            Paragraph(c_status,               styles["td"]),
        ])

    comp_table = Table(tdata, colWidths=cw, repeatRows=1)
    comp_table.setStyle(TableStyle([
        ("BACKGROUND",      (0,0),(-1,0),  BLACK),
        ("ROWBACKGROUNDS",  (0,1),(-1,-1), [WHITE, LIGHT_GRAY]),
        # Audited row highlight
        ("BACKGROUND",      (0,1),(-1,1),  colors.HexColor("#F0F0F0")),
        ("LINEBELOW",       (0,1),(-1,1),  1.5, DARK_GRAY),
        # Grid
        ("LINEBELOW",       (0,0),(-1,-1), 0.3, RULE_GRAY),
        ("VALIGN",          (0,0),(-1,-1), "MIDDLE"),
        ("TOPPADDING",      (0,0),(-1,-1), 7),
        ("BOTTOMPADDING",   (0,0),(-1,-1), 7),
        ("LEFTPADDING",     (0,0),(-1,-1), 8),
        ("RIGHTPADDING",    (0,0),(-1,-1), 8),
        ("ALIGN",           (1,0),(-1,-1), "CENTER"),
        ("ALIGN",           (0,0),(0,-1),  "LEFT"),
    ]))

    comp_body = [comp_table, Spacer(1,10)]
    if comp_txt and "[Narrative" not in comp_txt:
        comp_body += parse_paragraphs(comp_txt, styles)
    story += section(3, "Competitor Analysis", comp_body, styles)

    # ═════════════════════════════════════════════════════════════════════
    # SECTION 4 — OPPORTUNITIES
    # ═════════════════════════════════════════════════════════════════════
    opps = parse_bullets(opps_txt, styles) if (opps_txt and "[Narrative" not in opps_txt) \
           else [Paragraph("No data available.", styles["body"])]
    story += section(4, "Opportunities ABGrowthCo Can Exploit", opps, styles)

    # ═════════════════════════════════════════════════════════════════════
    # SECTION 5 — RECOMMENDED AD STRATEGY
    # ═════════════════════════════════════════════════════════════════════
    ads_body = []
    if ads_txt and "[Narrative" not in ads_txt:
        for block in re.split(r"\n{2,}", ads_txt.strip()):
            block = block.strip()
            if not block:
                continue
            m = re.match(r"\*\*(.*?)\*\*(.*)", block, re.DOTALL)
            if m:
                label_txt = m.group(1).strip().upper()
                body_txt  = strip_md(m.group(2).strip(" :—"))
                row = [[
                    Paragraph(label_txt, ParagraphStyle(
                        "sl", fontName="Helvetica-Bold", fontSize=8,
                        textColor=WHITE)),
                    Paragraph(body_txt, ParagraphStyle(
                        "sb", fontName="Helvetica", fontSize=8.5,
                        textColor=CHARCOAL, leading=13)),
                ]]
                ct = Table(row, colWidths=[USABLE_W*0.20, USABLE_W*0.80])
                ct.setStyle(TableStyle([
                    ("BACKGROUND",    (0,0),(0,0), DARK_GRAY),
                    ("BACKGROUND",    (1,0),(1,0), LIGHT_GRAY),
                    ("VALIGN",        (0,0),(-1,-1), "TOP"),
                    ("TOPPADDING",    (0,0),(-1,-1), 9),
                    ("BOTTOMPADDING", (0,0),(-1,-1), 9),
                    ("LEFTPADDING",   (0,0),(-1,-1), 10),
                    ("RIGHTPADDING",  (0,0),(-1,-1), 10),
                    ("LINEBELOW",     (0,0),(-1,-1), 0.4, RULE_GRAY),
                ]))
                ads_body.append(ct)
                ads_body.append(Spacer(1, 3))
            elif block.startswith(("- ", "• ", "- **", "• **")):
                ads_body += parse_bullets(block, styles)
            else:
                ads_body.append(Paragraph(strip_md(block), styles["body"]))
                ads_body.append(Spacer(1, 4))
    story += section(5, "Recommended Ad Strategy (Meta / Instagram)", ads_body, styles)

    # ═════════════════════════════════════════════════════════════════════
    # CLOSING CTA BANNER
    # ═════════════════════════════════════════════════════════════════════
    story.append(Spacer(1, 16))
    cta = Table([[
        Paragraph("READY TO GROW?", ParagraphStyle(
            "ch", fontName="Helvetica-Bold", fontSize=12,
            textColor=WHITE, spaceAfter=5)),
        Paragraph(
            "ABGrowthCo builds 90-day growth campaigns from audits exactly like "
            "this one — Meta &amp; Instagram advertising for local businesses. "
            "Let's turn these findings into revenue.",
            ParagraphStyle("cb", fontName="Helvetica", fontSize=9,
                           textColor=colors.HexColor("#D1D5DB"), leading=13)),
    ]], colWidths=[USABLE_W*0.28, USABLE_W*0.72])
    cta.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), BLACK),
        ("TOPPADDING",    (0,0),(-1,-1), 18),
        ("BOTTOMPADDING", (0,0),(-1,-1), 18),
        ("LEFTPADDING",   (0,0),(-1,-1), 18),
        ("RIGHTPADDING",  (0,0),(-1,-1), 14),
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
        ("LINEABOVE",     (0,0),(-1,0),  2.5, DARK_GRAY),
    ]))
    story.append(cta)

    doc.build(story)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Export audit as branded PDF")
    parser.add_argument("--business",          required=True)
    parser.add_argument("--location",          required=True)
    parser.add_argument("--reviews-file",      required=True)
    parser.add_argument("--ads-file",          required=True)
    parser.add_argument("--competitors-file",  required=True)
    parser.add_argument("--social-file",       required=True)
    parser.add_argument("--report-file",       required=True)
    parser.add_argument("--output",            required=True)
    args = parser.parse_args()

    os.makedirs(
        os.path.dirname(args.output) if os.path.dirname(args.output) else ".",
        exist_ok=True)

    print("Loading data files...")
    reviews     = load_json(args.reviews_file)
    ads         = load_json(args.ads_file)
    competitors = load_json(args.competitors_file)
    social      = load_json(args.social_file)

    if not os.path.exists(args.report_file):
        print(f"Error: report markdown not found: {args.report_file}", file=sys.stderr)
        sys.exit(1)
    with open(args.report_file) as f:
        report_md = f.read()

    logo_status = "found — will embed" if os.path.exists(LOGO_PATH) \
                  else "not found — placeholder will be used"
    print(f"Logo: {logo_status}")
    print("Building PDF...")
    build_pdf(args.business, args.location,
              reviews, ads, competitors, social,
              report_md, args.output)

    size_kb = os.path.getsize(args.output) // 1024
    print(f"PDF saved → {args.output}  ({size_kb} KB)")


if __name__ == "__main__":
    main()
