#!/usr/bin/env python3
"""
Mantri Sprint 1 Demo Video Builder
Renders slides as PNG images, generates audio with Gemini TTS, assembles with ffmpeg.
Falls back to macOS `say` if GOOGLE_API_KEY is not set.
"""

import os
import struct
import subprocess
import textwrap
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── Config ──────────────────────────────────────────────────────────────────
W, H = 1920, 1080
SPRINT1_DIR = Path(__file__).parent.parent / "demo" / "sprint1"
SLIDE_DIR   = SPRINT1_DIR / "slides"
AUDIO_DIR   = SPRINT1_DIR / "audio"
OUTPUT_VIDEO = str(SPRINT1_DIR / "sprint1_demo.mp4")

SLIDE_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# ── Colors ───────────────────────────────────────────────────────────────────
BG         = (15, 20, 30)       # near-black navy
ACCENT     = (64, 156, 255)     # blue
ACCENT2    = (255, 180, 60)     # gold
WHITE      = (255, 255, 255)
LIGHT_GRAY = (200, 210, 220)
MID_GRAY   = (130, 145, 160)
DIM_GRAY   = (60, 75, 90)
GREEN      = (80, 200, 120)
RED        = (255, 90, 90)

# ── Font helpers ─────────────────────────────────────────────────────────────
def font(size, bold=False):
    """Load SF Pro or fallback system font."""
    # DejaVu Sans first — full Unicode coverage (✓ ✗ → ● etc.)
    candidates = [
        "/Users/kunalc/anaconda3/pkgs/font-ttf-dejavu-sans-mono-2.37-hab24e00_0/fonts/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ]
    if bold:
        bold_candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ]
        candidates = bold_candidates + candidates
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def draw_text_wrapped(draw, text, x, y, max_width, fnt, color, line_spacing=1.4):
    """Draw word-wrapped text, return bottom y."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=fnt)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    line_height = int(fnt.size * line_spacing)
    for line in lines:
        draw.text((x, y), line, font=fnt, fill=color)
        y += line_height
    return y


def pill(draw, x, y, w, h, color, radius=12):
    """Draw a rounded rectangle."""
    draw.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=color)


# ── Base canvas ───────────────────────────────────────────────────────────────
def base_canvas():
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    # Subtle top accent bar
    draw.rectangle([0, 0, W, 6], fill=ACCENT)
    # Bottom strip
    draw.rectangle([0, H - 4, W, H], fill=DIM_GRAY)
    return img, draw


def add_footer(draw, slide_num, total):
    f = font(22)
    draw.text((60, H - 38), "Mantri · Sprint 1 Demo · March 2026", font=f, fill=DIM_GRAY)
    draw.text((W - 120, H - 38), f"{slide_num}/{total}", font=f, fill=DIM_GRAY)


# ── Slide definitions ─────────────────────────────────────────────────────────
def slide_01():
    img, draw = base_canvas()
    # Big brand word
    f_huge = font(110, bold=True)
    f_sub  = font(42)
    f_tag  = font(28)
    draw.text((W // 2, 320), "MANTRI", font=f_huge, fill=WHITE, anchor="mm")
    draw.text((W // 2, 450), "AI Operations Agent for Small Business", font=f_sub, fill=ACCENT, anchor="mm")
    draw.line([(W // 2 - 220, 500), (W // 2 + 220, 500)], fill=DIM_GRAY, width=2)
    draw.text((W // 2, 545), "Sprint 1 Demo  ·  March 2026", font=f_tag, fill=MID_GRAY, anchor="mm")
    draw.text((W // 2, 620), "Kunal Chowdhury", font=f_tag, fill=MID_GRAY, anchor="mm")
    add_footer(draw, 1, 11)
    return img


def slide_02():
    img, draw = base_canvas()
    f_h  = font(54, bold=True)
    f_b  = font(34)
    f_sm = font(28)

    draw.text((80, 70), "The Problem", font=f_h, fill=WHITE)
    draw.line([(80, 142), (580, 142)], fill=ACCENT, width=3)

    pain_points = [
        ("10–20 concurrent orders",   "Procurement, delivery, payment — all at once"),
        ("Dozens of WhatsApp groups", "Army units, suppliers, staff — all separate"),
        ("No system",                 "All tracking is in Ashish's head"),
        ("Things fall through gaps",  "Missed deliveries, untracked payments, silent drops"),
    ]

    y = 180
    for title, desc in pain_points:
        pill(draw, 80, y, 18, 54, ACCENT)
        draw.text((120, y + 4), title, font=f_b, fill=WHITE)
        draw.text((120, y + 46), desc, font=f_sm, fill=MID_GRAY)
        y += 110

    # Right side illustration box
    pill(draw, 1100, 160, 740, 720, DIM_GRAY, radius=20)
    f_ill = font(26)
    labels = [
        "SATA Bty Bn (client)",
        "Kapoor Steel (supplier)",
        "Sanbira / LG (supplier)",
        "All-Staff group",
        "1:1 Samita",
        "1:1 transporter",
    ]
    draw.text((1470, 175), "Ashish's Active Groups", font=font(28, bold=True), fill=ACCENT, anchor="mm")
    for i, label in enumerate(labels):
        gy = 220 + i * 90
        pill(draw, 1120, gy, 700, 66, (30, 42, 58), radius=10)
        draw.text((1140, gy + 14), f"● {label}", font=f_ill, fill=LIGHT_GRAY)

    add_footer(draw, 2, 11)
    return img


def slide_03():
    img, draw = base_canvas()
    f_h = font(54, bold=True)
    f_b = font(34)
    f_sm = font(28)

    draw.text((80, 70), "The Solution", font=f_h, fill=WHITE)
    draw.line([(80, 142), (480, 142)], fill=ACCENT, width=3)

    features = [
        ("◉  Passive observer",   "Monitors messages — never posts to groups"),
        ("⊕  Cross-group linking", "Correlates the same order across multiple threads"),
        ("◈  Entity resolution",   "Understands 'Kapoor ji', 'Kapoor Steel', 'KS' → same entity"),
        ("◆  Implicit task detection", "Surfaces tasks implied by context, not just explicit requests"),
        ("▲  Priority alerting",   "Flags what needs action before it's too late"),
    ]

    y = 175
    for icon_title, desc in features:
        pill(draw, 80, y, W - 160, 86, (25, 35, 52), radius=14)
        draw.text((110, y + 12), icon_title, font=f_b, fill=WHITE)
        draw.text((110, y + 52), desc, font=f_sm, fill=MID_GRAY)
        y += 100

    add_footer(draw, 3, 11)
    return img


def slide_04():
    img, draw = base_canvas()
    f_h  = font(54, bold=True)
    f_b  = font(32)
    f_sm = font(26)
    f_xs = font(22)

    draw.text((80, 70), "The Prototype", font=f_h, fill=WHITE)
    draw.line([(80, 142), (380, 142)], fill=ACCENT2, width=3)

    # Left: order facts
    facts = [
        ("Army unit:", "SATA Battery"),
        ("Order:",     "14 procurement items"),
        ("Suppliers:", "Voltas, LG / Sanbira, + 2 more"),
        ("Groups:",    "4 WhatsApp threads merged"),
        ("Data:",      "Real messages, names replaced"),
    ]
    y = 175
    for label, value in facts:
        draw.text((100, y), label, font=f_sm, fill=MID_GRAY)
        draw.text((310, y), value, font=f_b, fill=WHITE)
        y += 72

    # Right: agent output highlights
    pill(draw, 1050, 155, 810, 760, (20, 32, 48), radius=18)
    draw.text((1460, 180), "Agent Surfaced", font=font(30, bold=True), fill=ACCENT, anchor="mm")

    highlights = [
        (GREEN, "✓", "All 14 itemss identified"),
        (GREEN, "✓", "7 ambiguities flagged for human review"),
        (GREEN, "✓", "21-day Amazon silent drop detected"),
        (GREEN, "✓", "Missing supplier threads flagged"),
        (GREEN, "✓", "Cross-thread payment correlation"),
        (ACCENT2, "~", "2 items not discretely named"),
        (ACCENT2, "~", "Post-delivery checklist not explicit"),
    ]
    hy = 220
    for color, mark, text in highlights:
        draw.text((1080, hy), mark, font=f_b, fill=color)
        draw.text((1130, hy), text, font=f_sm, fill=LIGHT_GRAY)
        hy += 76

    add_footer(draw, 4, 11)
    return img


def slide_05():
    img, draw = base_canvas()
    f_h  = font(54, bold=True)
    f_b  = font(32)
    f_sm = font(26)

    draw.text((80, 70), "Evaluation Framework", font=f_h, fill=WHITE)
    draw.line([(80, 142), (600, 142)], fill=ACCENT, width=3)

    # 6 quality dimensions
    dims = [
        ("Recall",              "Missing a task = most costly failure"),
        ("Entity Accuracy",     "Right customer/supplier/item links"),
        ("Cross-thread Correlation", "Same order unified across groups"),
        ("Next Step Quality",   "Actionable, specific, not generic"),
        ("Implicit Task Detection", "Cadence + reactive tasks both covered"),
        ("Ambiguity Flagging",  "Flag unclear → don't guess"),
    ]

    y = 175
    for i, (dim, desc) in enumerate(dims):
        num_color = ACCENT if i < 3 else ACCENT2
        pill(draw, 80, y, 34, 50, num_color, radius=8)
        draw.text((97, y + 6), str(i + 1), font=font(28, bold=True), fill=BG)
        draw.text((130, y + 4), dim, font=f_b, fill=WHITE)
        draw.text((130, y + 40), desc, font=f_sm, fill=MID_GRAY)
        y += 88

    # Right: level pyramid
    pill(draw, 1100, 160, 750, 760, (20, 32, 48), radius=18)
    draw.text((1475, 185), "Test Case Levels", font=font(30, bold=True), fill=ACCENT, anchor="mm")

    levels = [
        (RED,    "L3", "10 cases", "Complex: concurrent orders,\ninterleaved messages, real data"),
        (ACCENT2,"L2", "11 cases", "Intermediate: abbreviations,\ncross-thread challenges"),
        (GREEN,  "L1", "10 cases", "Simple: single thread,\nsingle entity, clear signals"),
    ]
    ly = 235
    for color, lbl, count, desc in levels:
        pill(draw, 1130, ly, 690, 130, (30, 42, 58), radius=12)
        pill(draw, 1145, ly + 15, 70, 100, color, radius=10)
        draw.text((1183, ly + 50), lbl, font=font(30, bold=True), fill=BG, anchor="mm")
        draw.text((1240, ly + 18), count, font=f_b, fill=WHITE)
        draw_text_wrapped(draw, desc, 1240, ly + 52, 540, f_sm, MID_GRAY, line_spacing=1.3)
        ly += 148

    draw.text((1475, 885), "31 test cases total · LLM-as-judge auto-scorer", font=font(24), fill=ACCENT, anchor="mm")

    add_footer(draw, 6, 11)
    return img


def slide_06():
    img, draw = base_canvas()
    f_h = font(54, bold=True)
    f_b = font(34)
    f_sm = font(28)

    draw.text((80, 70), "Sprint 1 Results", font=f_h, fill=WHITE)
    draw.line([(80, 142), (460, 142)], fill=GREEN, width=3)

    # Synthetic results
    pill(draw, 80, 185, 840, 260, (20, 32, 48), radius=18)
    draw.text((500, 215), "Synthetic Cases", font=font(34, bold=True), fill=WHITE, anchor="mm")
    draw.text((500, 265), "16", font=font(90, bold=True), fill=GREEN, anchor="mm")
    draw.text((500, 355), "/ 16  PASS  after 3 prompt iterations", font=f_sm, fill=MID_GRAY, anchor="mm")

    # Prompt iteration
    pill(draw, 80, 470, 840, 200, (20, 32, 48), radius=18)
    draw.text((200, 500), "Prompt Iterations", font=font(30, bold=True), fill=ACCENT)
    iters = [("Run 1", "11/16"), ("Run 2", "13/16"), ("Run 3", "16/16")]
    ix = 120
    for label, score in iters:
        draw.text((ix, 545), label, font=f_sm, fill=MID_GRAY)
        draw.text((ix, 580), score, font=f_b, fill=WHITE)
        ix += 255

    # SATA real case
    pill(draw, 980, 185, 860, 480, (20, 32, 48), radius=18)
    draw.text((1410, 220), "SATA Real Case", font=font(34, bold=True), fill=WHITE, anchor="mm")
    draw.text((1410, 300), "88", font=font(120, bold=True), fill=GREEN, anchor="mm")
    draw.text((1410, 430), "/ 100", font=f_b, fill=LIGHT_GRAY, anchor="mm")
    draw.text((1410, 480), "PASS", font=font(44, bold=True), fill=GREEN, anchor="mm")
    draw.text((1410, 540), "Complex real-world order, 4 groups", font=f_sm, fill=MID_GRAY, anchor="mm")
    draw.text((1410, 580), "claude-sonnet-4-6", font=font(24), fill=DIM_GRAY, anchor="mm")

    add_footer(draw, 7, 11)
    return img


def slide_07():
    img, draw = base_canvas()
    f_h = font(54, bold=True)
    f_b = font(36)
    f_sm = font(28)
    f_xs = font(24)

    draw.text((80, 70), "Model Comparison: Claude vs Gemini", font=f_h, fill=WHITE)
    draw.line([(80, 142), (780, 142)], fill=ACCENT2, width=3)

    # Claude card
    pill(draw, 80, 185, 840, 680, (20, 36, 28), radius=20)
    draw.text((500, 225), "Claude Sonnet 4.6", font=font(34, bold=True), fill=GREEN, anchor="mm")
    draw.text((500, 310), "88", font=font(130, bold=True), fill=GREEN, anchor="mm")
    draw.text((500, 460), "/ 100  ·  PASS", font=f_b, fill=WHITE, anchor="mm")

    claude_notes = [
        "All 14 item types identified",
        "7 ambiguity flags raised",
        "Cross-thread correlation ✓",
        "Output complete",
    ]
    ny = 510
    for note in claude_notes:
        draw.text((150, ny), f"✓  {note}", font=f_sm, fill=LIGHT_GRAY)
        ny += 48

    draw.text((500, 780), "~$0.024 / order", font=f_xs, fill=MID_GRAY, anchor="mm")

    # Gemini card
    pill(draw, 980, 185, 840, 680, (40, 20, 20), radius=20)
    draw.text((1400, 225), "Gemini 2.5 Flash", font=font(34, bold=True), fill=RED, anchor="mm")
    draw.text((1400, 310), "52", font=font(130, bold=True), fill=RED, anchor="mm")
    draw.text((1400, 460), "/ 100  ·  PARTIAL FAIL", font=f_b, fill=WHITE, anchor="mm")

    gemini_notes = [
        "Output truncated mid-sentence",
        "Half task list never delivered",
        "No supplier-gap flags raised",
        "Batteries, shoes absent",
    ]
    ny = 510
    for note in gemini_notes:
        draw.text((1010, ny), f"✗  {note}", font=f_sm, fill=LIGHT_GRAY)
        ny += 48

    draw.text((1400, 780), "~$0.004 / order  (cheaper, but unreliable)", font=f_xs, fill=MID_GRAY, anchor="mm")

    draw.text((W // 2, 940), "Verdict: Claude Sonnet remains production model at current complexity", font=f_sm, fill=ACCENT, anchor="mm")

    add_footer(draw, 8, 11)
    return img


def slide_08():
    img, draw = base_canvas()
    f_h = font(54, bold=True)
    f_b = font(34)
    f_sm = font(28)

    draw.text((80, 70), "Gap Found: Cadence Implicit Tasks", font=f_h, fill=WHITE)
    draw.line([(80, 142), (720, 142)], fill=ACCENT2, width=3)

    # Two columns
    pill(draw, 80, 185, 840, 680, (20, 36, 28), radius=20)
    draw.text((500, 220), "Reactive Implicit Tasks", font=font(30, bold=True), fill=GREEN, anchor="mm")
    draw.text((500, 265), "✓  Handled well", font=f_b, fill=GREEN, anchor="mm")
    reactives = [
        "Supplier silence → follow-up inferred",
        "21-day gap → silent drop flagged",
        "Missing OTG rate → blocker surfaced",
        "Ambiguous payment → escalated",
    ]
    ry = 310
    for r in reactives:
        draw.text((120, ry), f"✓  {r}", font=f_sm, fill=LIGHT_GRAY)
        ry += 62

    pill(draw, 980, 185, 840, 680, (40, 30, 10), radius=20)
    draw.text((1400, 220), "Cadence Implicit Tasks", font=font(30, bold=True), fill=ACCENT2, anchor="mm")
    draw.text((1400, 265), "✗  Confirmed gap", font=f_b, fill=ACCENT2, anchor="mm")
    cadences = [
        "Pre-dispatch checklist review missed",
        "Final payment confirmation missed",
        "Stage-triggered milestones absent",
        "Procedural steps not in prompt",
    ]
    cy = 310
    for c in cadences:
        draw.text((1010, cy), f"✗  {c}", font=f_sm, fill=LIGHT_GRAY)
        cy += 62

    draw.text((W // 2, 900), "Sprint 2 fix: task-type subtask checklists injected into prompt", font=f_sm, fill=ACCENT, anchor="mm")
    draw.text((W // 2, 945), "Precursor to Sprint 3 task lifecycle state machine", font=font(24), fill=MID_GRAY, anchor="mm")

    add_footer(draw, 9, 11)
    return img


def slide_09():
    img, draw = base_canvas()
    f_h = font(54, bold=True)
    f_b = font(32)
    f_sm = font(28)

    draw.text((80, 70), "What's Next: Sprint 2", font=f_h, fill=WHITE)
    draw.line([(80, 142), (500, 142)], fill=ACCENT, width=3)

    items = [
        ("Sprint 2  (by Apr 12)", ACCENT, [
            "Inject task-type subtask checklists into prompt",
            "User research: Ashish 1:1 + staff group session",
            "Live monitoring system design",
            "Validate extraction with Ashish on real cases",
        ]),
        ("Sprint 3  (by Apr 26)", MID_GRAY, [
            "Live WhatsApp ingestion via Baileys",
            "SQLite + FastAPI backend",
            "Task graph dashboard",
            "Ashish using it in production",
        ]),
    ]

    y = 185
    for sprint_label, color, tasks in items:
        pill(draw, 80, y, W - 160, 34, color, radius=0)
        draw.text((100, y + 4), sprint_label, font=font(26, bold=True), fill=BG)
        y += 52
        for task in tasks:
            draw.text((110, y), f"→  {task}", font=f_sm, fill=LIGHT_GRAY)
            y += 56
        y += 30

    add_footer(draw, 10, 11)
    return img


def slide_10():
    img, draw = base_canvas()
    f_h  = font(54, bold=True)
    f_b  = font(32)
    f_sm = font(27)
    f_xs = font(23)

    draw.text((80, 70), "Case Extractor Tool", font=f_h, fill=WHITE)
    draw.line([(80, 142), (520, 142)], fill=ACCENT2, width=3)

    # Left: what it does
    steps = [
        ("Input",    "Raw WhatsApp .txt exports + media files"),
        ("Filter",   "Time-window across multiple groups"),
        ("Annotate", "Claude Vision: payment screenshots, invoices, challans"),
        ("Output",   "Structured threads.txt — exact agent input format"),
    ]
    y = 185
    DESC_MAX_W = 590   # x=260 to x=850, safely inside the pill (80+860=940)
    PILL_H     = 120
    for label, desc in steps:
        pill(draw, 80, y, 860, PILL_H, (25, 38, 55), radius=14)
        pill(draw, 96, y + 22, 130, 76, ACCENT2, radius=10)
        draw.text((161, y + 47), label, font=font(26, bold=True), fill=BG, anchor="mm")
        draw_text_wrapped(draw, desc, 260, y + 20, DESC_MAX_W, f_sm, WHITE, line_spacing=1.35)
        y += PILL_H + 14

    # Right: two modes
    pill(draw, 1000, 160, 860, 760, (20, 32, 48), radius=18)
    draw.text((1430, 195), "Two Modes", font=font(34, bold=True), fill=ACCENT, anchor="mm")

    draw.text((1030, 240), "Case mode", font=font(30, bold=True), fill=ACCENT2)
    draw.text((1030, 280), "Driven by metadata.json", font=f_sm, fill=LIGHT_GRAY)
    draw.text((1030, 315), "Defines case ID, window, threads,", font=f_xs, fill=MID_GRAY)
    draw.text((1030, 343), "expected output, pass criteria", font=f_xs, fill=MID_GRAY)
    pill(draw, 1030, 370, 800, 52, (30, 42, 58), radius=8)
    draw.text((1044, 383), "python scripts/case_extractor.py --case tests/evals/R3-C-L3-02/", font=font(19), fill=LIGHT_GRAY)

    draw.line([(1030, 440), (1820, 440)], fill=DIM_GRAY, width=1)

    draw.text((1030, 460), "Ad-hoc mode", font=font(30, bold=True), fill=ACCENT2)
    draw.text((1030, 500), "Quick exploration before defining a case", font=f_sm, fill=LIGHT_GRAY)
    draw.text((1030, 538), "Pass --start, --end, --chats on the CLI", font=f_xs, fill=MID_GRAY)

    draw.text((1430, 840), "Vision annotation: payment_confirmation · proforma_invoice", font=f_xs, fill=MID_GRAY, anchor="mm")
    draw.text((1430, 875), "delivery_challan · order_list · payment_ledger", font=f_xs, fill=MID_GRAY, anchor="mm")

    add_footer(draw, 5, 11)
    return img


def slide_11():
    img, draw = base_canvas()
    f_h    = font(54, bold=True)
    f_sub  = font(34)
    f_sm   = font(28)

    draw.text((W // 2, 190), "Sprint 1 Complete", font=f_h, fill=WHITE, anchor="mm")
    draw.line([(W // 2 - 280, 248), (W // 2 + 280, 248)], fill=ACCENT, width=3)

    deliverables = [
        "Problem defined & documented",
        "SATA prototype: real data, 4 groups, 14 items — PASS",
        "Case extractor: WhatsApp exports → structured test inputs",
        "31-case evaluation framework + LLM-as-judge scorer",
        "Model comparison: Claude 88/100 vs Gemini 52/100",
        "Live monitoring & message router design complete",
    ]

    y = 278
    for d in deliverables:
        pill(draw, W // 2 - 520, y, 1040, 60, (25, 38, 55), radius=10)
        draw.text((W // 2, y + 14), f"✓  {d}", font=f_sm, fill=LIGHT_GRAY, anchor="mm")
        y += 76

    draw.text((W // 2, 870), "Final Demo target: May 1, 2026", font=f_sub, fill=ACCENT, anchor="mm")
    draw.text((W // 2, 930), "Mantri · Kunal Chowdhury", font=font(26), fill=MID_GRAY, anchor="mm")

    add_footer(draw, 11, 11)
    return img


# ── Voiceover scripts ─────────────────────────────────────────────────────────
# Order must match SLIDE_FUNCS exactly:
# 01 Title | 02 Problem | 03 Solution | 04 SATA | 05 Case Extractor |
# 06 Eval Framework | 07 Results | 08 Model Comparison | 09 Cadence Gap |
# 10 Sprint 2 | 11 Wrap-up
VOICEOVERS = [
    # Slide 1 — Title
    "This is the Sprint 1 demo for Mantri — an A.I. operations agent for an Army supply business.",

    # Slide 2 — The Problem
    "Ashish runs an Army supply business in Guwahati. He manages procurement, delivery, and client coordination "
    "entirely through WhatsApp — across dozens of groups and one-on-ones. "
    "At any moment he's tracking 10 to 20 concurrent orders involving multiple suppliers, Army units, and staff members. "
    "The problem: there is no system. Tasks fall through the gaps. A supplier confirms delivery in one group, "
    "payment is discussed in another, and Ashish has to mentally correlate it all in real time — "
    "in Hinglish, with informal names and abbreviations. At scale, things get missed.",

    # Slide 3 — The Solution
    "Mantri is a background A.I. agent that monitors Ashish's WhatsApp messages. "
    "It extracts tasks, tracks their status across groups, and surfaces what needs attention — "
    "without ever posting or interfering with Ashish's existing workflows. "
    "The agent speaks the same informal language Ashish's team uses: Hinglish, location shorthand, "
    "officer titles, supplier nicknames. It doesn't require Ashish to change how he works.",

    # Slide 4 — The Prototype
    "In Sprint 1 we built and tested the extraction agent on the SATA order — "
    "a real, complex multi-item procurement from a real Army unit. "
    "The order spanned four WhatsApp groups: a client group, two supplier groups, and an internal coordination group. "
    "14 items. 5 suppliers. Multiple concurrent payment and delivery threads. "
    "We ran the agent on the complete four-thread context. "
    "It identified all major items, correctly attributed them to the right suppliers, "
    "flagged 7 ambiguous correlations for human review, and surfaced implicit tasks — "
    "like a 21-day gap on an Amazon order with no follow-up — without any explicit mention in the messages.",

    # Slide 5 — Case Extractor Tool
    "To build test cases from real data, we wrote a case extractor tool. "
    "It takes raw WhatsApp chat exports, filters a time window across multiple groups, "
    "and uses Claude Vision to annotate attached images and documents — "
    "payment screenshots, invoices, and delivery challans — "
    "producing a structured multi-thread input in exactly the format the evaluation agent expects. "
    "Cases can be defined with a metadata file for repeatability, "
    "or extracted ad-hoc for quick exploration. "
    "This tool is what bridges Ashish's real WhatsApp data and our evaluation pipeline.",

    # Slide 6 — Evaluation Framework
    "To measure quality systematically, we designed an evaluation framework with six dimensions: "
    "task recall, entity accuracy, cross-thread correlation, next step quality, implicit task detection, and ambiguity flagging. "
    "The hardest and most important is recall — missing a task entirely is the most costly failure mode. "
    "We built 31 test cases across three complexity levels. "
    "Level 1 covers single-thread, single-entity scenarios. "
    "Level 2 adds abbreviation and cross-thread challenges. "
    "Level 3 handles the hardest cases: interleaved messages, concurrent orders, and real multi-supplier complexity. "
    "Each run is automatically scored by a second L.L.M. acting as judge, evaluating against structured pass criteria.",

    # Slide 7 — Sprint 1 Results
    "On the 16 synthetic test cases, we ran three rounds of prompt iteration. "
    "The first run scored 11 out of 16. "
    "After calibrating entity resolution, adding a separation-default rule, "
    "and fixing the structural decomposition for unidentified clients, we reached 16 out of 16. "
    "On the real SATA case — the hardest case in the set — Claude Sonnet scored 88 out of 100. "
    "The main gaps were at the margin: two item categories not surfaced as discrete tasks, "
    "and post-delivery checklist items not made explicit. The core logic worked.",

    # Slide 8 — Model Comparison
    "We also evaluated Gemini 2.5 Flash as a cheaper alternative. "
    "On the same SATA case, Gemini scored 52 out of 100 — a partial fail. "
    "The critical failure was output truncation: the response cut off mid-sentence, "
    "meaning roughly half the task list was never delivered. "
    "Items like batteries and basketball shoes were absent entirely. "
    "No supplier-thread gap flags were raised. "
    "The verdict: Gemini 2.5 Flash is not reliable enough for this task at current complexity. "
    "Claude Sonnet remains the production model.",

    # Slide 9 — Cadence Gap
    "Testing also revealed a confirmed gap: cadence implicit tasks. "
    "The agent handles reactive implicit tasks well — "
    "inferring a follow-up from a supplier's silence, for example. "
    "But procedural milestones — things that should always happen at a certain stage of every order, "
    "regardless of what messages say — were missed. Pre-dispatch checklist review. Final payment confirmation. "
    "This is the highest-priority quality risk going into Sprint 2.",

    # Slide 10 — Sprint 2 Direction
    "Sprint 2 builds on this. "
    "We're injecting task-type subtask checklists into the prompt — "
    "empirically derived from Ashish's historical orders — to catch procedural milestones. "
    "We're also designing the live monitoring system and conducting user research with Ashish and his staff. "
    "The goal by Sprint 2 end: reliable extraction on live data, with Ashish validating in real conditions. "
    "Sprint 3 target is full live deployment — Ashish using it.",

    # Slide 11 — Wrap-up
    "Sprint 1 delivered: problem defined, extraction prototype validated on real data, "
    "case extractor tool built, 31-case evaluation framework live, "
    "evaluation data workflow complete, and the first model comparison done. "
    "The live monitoring and message router designs are also complete, setting up the Sprint 3 build. "
    "Final demo target: May 1st. Thank you.",
]

# Duration per slide (seconds) — approximate, actual is driven by audio length
DURATIONS = [12, 35, 28, 35, 30, 40, 33, 32, 28, 28, 20]

SLIDE_FUNCS = [
    slide_01, slide_02, slide_03, slide_04, slide_10,   # 1-5: case extractor now pos 5
    slide_05, slide_06, slide_07, slide_08, slide_09, slide_11,  # 6-11
]


def render_slides():
    print("Rendering slides...")
    for i, (fn, vo) in enumerate(zip(SLIDE_FUNCS, VOICEOVERS)):
        path = SLIDE_DIR / f"slide_{i+1:02d}.png"
        img = fn()
        img.save(path)
        print(f"  ✓ {path}")


def _gemini_tts(text: str, wav_path: Path, voice: str = "Aoede"):
    """Generate speech via Gemini 2.5 Flash TTS → WAV (24 kHz, 16-bit, mono)."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise EnvironmentError("GOOGLE_API_KEY not set")

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model="gemini-2.5-flash-preview-tts",
        contents=types.Content(
            role="user",
            parts=[types.Part(text=text)],
        ),
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice,
                    )
                )
            ),
        ),
    )

    candidate = response.candidates[0]
    if candidate.content is None or not candidate.content.parts:
        # Model generated text instead of audio — wrap text in quotes to force audio path
        response = client.models.generate_content(
            model="gemini-2.5-flash-preview-tts",
            contents=types.Content(
                role="user",
                parts=[types.Part(text=f'Read aloud: "{text}"')],
            ),
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                    )
                ),
            ),
        )
        candidate = response.candidates[0]

    pcm_data = candidate.content.parts[0].inline_data.data

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)       # 16-bit
        wf.setframerate(24000)
        wf.writeframes(pcm_data)


def _say_tts(text: str, wav_path: Path):
    """Fallback: macOS `say` → AIFF → WAV (44.1 kHz)."""
    aiff_path = wav_path.with_suffix(".aiff")
    subprocess.run(
        ["say", "-v", "Samantha", "-r", "185", "-o", str(aiff_path), text],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(aiff_path), str(wav_path)],
        check=True, capture_output=True,
    )
    aiff_path.unlink(missing_ok=True)


def generate_audio(gemini_voice: str = "Kore", overwrite: bool = True):
    use_gemini = bool(os.environ.get("GOOGLE_API_KEY"))
    engine = f"Gemini TTS (voice: {gemini_voice})" if use_gemini else "macOS `say` (fallback)"
    print(f"Generating audio with {engine}...")

    import time
    for i, vo in enumerate(VOICEOVERS):
        wav_path = AUDIO_DIR / f"slide_{i+1:02d}.wav"
        if not overwrite and wav_path.exists():
            print(f"  – {wav_path} (skipped, exists)")
            continue
        for attempt in range(3):
            try:
                if use_gemini:
                    _gemini_tts(vo, wav_path, voice=gemini_voice)
                else:
                    _say_tts(vo, wav_path)
                break
            except Exception as e:
                if attempt < 2:
                    print(f"  ⚠ slide {i+1:02d} attempt {attempt+1} failed: {e} — retrying in 5s")
                    time.sleep(5)
                else:
                    raise
        print(f"  ✓ {wav_path}")


def get_audio_duration(audio_path):
    """Get duration of an audio file in seconds using ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(audio_path)],
        capture_output=True, text=True, check=True
    )
    return float(result.stdout.strip())


def assemble_video():
    """Single-pass assembly using filter_complex concat — no intermediate segments."""
    print("Assembling video (single-pass filter_complex)...")

    n = len(SLIDE_FUNCS)
    cmd = ["ffmpeg", "-y"]

    # Add all inputs: alternating slide image + audio wav
    durations = []
    for i in range(n):
        slide_path = SLIDE_DIR / f"slide_{i+1:02d}.png"
        audio_path = AUDIO_DIR / f"slide_{i+1:02d}.wav"
        duration = get_audio_duration(audio_path) + 0.5
        durations.append(duration)
        cmd += ["-loop", "1", "-t", str(duration), "-i", str(slide_path)]
        cmd += ["-i", str(audio_path)]

    # Build filter_complex: scale each video input, pad audio to slide duration, then concat
    filter_parts = []
    for i in range(n):
        vi = i * 2       # video input index
        ai = i * 2 + 1   # audio input index
        d  = durations[i]
        filter_parts.append(f"[{vi}:v]scale=1920:1080,setsar=1,fps=25[v{i}]")
        # apad ensures audio fills exactly the slide duration — no trailing bleed
        filter_parts.append(f"[{ai}:a]aresample=44100,apad=whole_dur={d}[a{i}]")

    v_inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
    filter_parts.append(f"{v_inputs}concat=n={n}:v=1:a=1[outv][outa]")

    cmd += ["-filter_complex", ";".join(filter_parts)]
    cmd += ["-map", "[outv]", "-map", "[outa]"]
    cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += [OUTPUT_VIDEO]

    for i, d in enumerate(durations):
        print(f"  slide {i+1:02d}  ({d:.1f}s)")

    subprocess.run(cmd, check=True, capture_output=True)
    print(f"\n✅  Video written to: {OUTPUT_VIDEO}")


if __name__ == "__main__":
    import sys
    os.chdir(SPRINT1_DIR)

    # Optional: --voice <name> to pick a Gemini voice
    voice = "Kore"
    if "--voice" in sys.argv:
        voice = sys.argv[sys.argv.index("--voice") + 1]

    # --audio-only skips slide rendering and video assembly (re-generate audio only)
    audio_only = "--audio-only" in sys.argv
    # --video-only skips slide rendering and audio (re-assemble video only)
    video_only = "--video-only" in sys.argv

    # --no-overwrite keeps existing WAV files (useful when retrying failed slides)
    overwrite = "--no-overwrite" not in sys.argv

    if not audio_only and not video_only:
        render_slides()
    if not video_only:
        generate_audio(gemini_voice=voice, overwrite=overwrite)
    if not audio_only:
        assemble_video()
