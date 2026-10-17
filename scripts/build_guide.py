"""Build the Persian user guide PDF from docs/guide/guide-fa.html and its screenshots.

    python scripts/build_guide.py            # docs/MarbleTrack-Guide-FA.pdf (the Windows build bundles it; Guide button)

The screenshots in docs/guide/img come from the app in demo mode (`python -m marbletrack_app --demo`,
Persian interface), taken by scripts/guide_screenshots.py, which also records where each button is
(img/boxes.json). Every <shot> in the HTML becomes a figure with numbered markers on the buttons
listed in LEGENDS below, and the numbered list under it; the PDF is rendered by Chromium (Playwright).
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "guide"
OUT = ROOT / "docs" / "MarbleTrack-Guide-FA.pdf"
FONT = ROOT / "marbletrack_app" / "web" / "fonts" / "Vazirmatn.woff2"

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

# what each marked element is, per screenshot (selectors as recorded in img/boxes.json)
LEGENDS = {
    "00-first-run": {
        "#mapsBtn": "<b>نقشه‌ها</b>: باز کردن، ساختن، کپی و خروجی گرفتن از نقشهٔ مسیرها.",
        "#demoAll": "<b>راه‌اندازی خودکار دوربین‌های نمایشی</b>: فقط در حالت نمایشی دیده می‌شود.",
        "#guideBtn": "<b>راهنما</b>: همین راهنما را باز می‌کند.",
        "#langBtn": "<b>English / فارسی</b>: تغییر زبان برنامه.",
        "#modePill": "حالت فعلی برنامه: <b>راه‌اندازی</b> یا <b>زنده</b>.",
        "#rail .step.on": "<b>نوار مرحله‌ها</b>: هفت مرحلهٔ راه‌اندازی؛ تیک سبز یعنی آن مرحله کامل است.",
        "#foot .msg": "پیام وضعیت مرحله: اولین کاری که هنوز مانده را می‌گوید.",
        "#next": "<b>ادامه</b>: رفتن به مرحلهٔ بعد. دکمهٔ «قبلی» در طرف دیگر نوار است.",
    },
    "01-race": {
        "#pname": "<b>نام مسابقه</b>: نام این نقشه، مثلاً «مسیر قرعهٔ ۱۲».",
        "#plen": "<b>طول مسیر</b>: از دروازهٔ شروع تا انتهای مسیر خروجی، به متر.",
        "#pfin": "<b>خط پایان در</b>: فاصلهٔ خط پایان از دروازهٔ شروع.",
        "#plaps": "<b>تعداد دور</b>: برای مسیرهای حلقه‌ای بیشتر از ۱.",
        "#pspc": "<b>فاصلهٔ علامت‌های مسیر</b>: پیش‌فرض ۰٫۲ متر (هر ۲۰ سانتی‌متر).",
        "details.more summary": "<b>تنظیمات بیشتر مسیر</b>: باز کردن تنظیمات تکمیلی (تصویر بعد).",
        "#madd": "<b>افزودن توپ</b>.",
        "#mlist .marble-row": "هر توپ: رنگ نمایشی، نام، و دکمهٔ حذف (×).",
    },
    "02-race-more": {
        "#pstart": "<b>شروع ساعت مسابقه در</b>: ساعت وقتی راه می‌افتد که اولین توپ از این فاصله بگذرد.",
        "#pchan": "<b>عرض کانال</b>: عرض داخلی مسیر به میلی‌متر.",
        "#pdia": "<b>قطر توپ</b>: برای توپ‌های شما ۲۵ میلی‌متر.",
        "#pblind": "<b>تونل‌ها و جاهای کور</b>: بازه‌هایی که هیچ دوربینی نمی‌بیند.",
        "#pwide": "<b>بخش‌های پهن‌تر کانال</b>: منطقه‌های مانع که از عرض معمول پهن‌ترند.",
        "#pauto": "شروع خودکار مسابقهٔ جدید وقتی توپ‌ها دوباره روی مسیر می‌آیند.",
        "#prack": "تأیید خودکار نتیجه وقتی دوربین قفسهٔ پایان با جدول یکی است.",
    },
    "03-maps": {
        "#mname": "نام نقشهٔ باز.",
        "#mnotes": "<b>یادداشت‌ها</b>: مثلاً شمارهٔ قرعه یا تغییرات مسیر.",
        "#mexport": "<b>خروجی گرفتن (zip)</b> از همین نقشه، برای پشتیبان یا رایانهٔ دیگر.",
        "#nname": "نام مسیر جدید.",
        "#nempty": "<b>ساختن مسیر جدید</b>: توپ‌ها، دوربین‌ها و خروجی‌ها می‌مانند.",
        "#mimport": "<b>ورود فایل zip</b>: باز کردن نقشه‌ای که قبلاً خروجی گرفته شده.",
        ".maprow [data-copy]": "<b>کپی به‌عنوان مسیر جدید</b>: شروع از روی یک نقشهٔ قدیمی.",
        ".maprow [data-export]": "<b>خروجی</b> از هر نقشهٔ فهرست. برای نقشه‌های دیگر دکمهٔ <b>باز کردن</b> هم هست.",
    },
    "04-cameras": {
        "#find": "<b>یافتن دوربین‌ها</b>: جست‌وجوی دوباره (NDI، USB، Basler).",
        ".cam .order": "جایگاه دوربین: «بخش ۱، ۲، …»، «خط پایان» یا «قفسهٔ پایان».",
        ".cam .name": "<b>نام دوربین</b>: برای خودتان، مثلاً «پیچ مارپیچ».",
        ".cam .cam-meta": "رزولوشن، فریم بر ثانیه و نوع اتصال.",
        ".cam .toggle": "<b>استفاده از این دوربین</b>: روشن یا خاموش.",
        ".cam [data-mv='1']": "<b>فلش‌ها</b>: جابه‌جایی دوربین به ترتیب طول مسیر.",
        ".cam select": "<b>نقش دوربین</b>: یک بخش از مسیر، خط پایان، یا قفسهٔ پایان.",
    },
    "05-waypoints-empty": {
        "#tabs": "<b>زبانهٔ دوربین‌ها</b>: نقطهٔ سبز یعنی آن دوربین آماده است.",
        "#view": "تصویر زندهٔ دوربین: روی علامت‌های مسیر همین‌جا کلیک کنید.",
        "#side .note": "راهنمای قدم بعدی.",
    },
    "06-waypoints-placed": {
        "#tabs .tab-btn.on": "دوربین انتخاب‌شده.",
        ".viewer .lbl": "<b>برچسب فاصله</b>: برای تغییر عدد، روی آن کلیک کنید.",
        "#side .kv": "خلاصه: تعداد نقطه‌ها، بازهٔ پوشش، فاصله و «جزئیات» (میلی‌متر در هر پیکسل).",
        "#wscale": "<b>پهنای نوار جست‌وجو</b>: نوار آبی باید کل کانال را بپوشاند.",
        "#undo": "<b>برگرداندن</b> آخرین نقطه (Ctrl+Z).",
        "#stretch": "<b>تکهٔ جدید</b>: وقتی دوربین بخش دیگری از مسیر را هم می‌بیند.",
        "#clear": "<b>پاک کردن همه</b>ٔ نقطه‌های این دوربین.",
        "#auto": "<b>چیدن خودکار</b>: فقط برای دوربین‌های نمایشی.",
    },
    "06b-waypoint-popover": {
        "#pop input": "فاصلهٔ درست این نقطه به متر.",
        "#pop [data-ok]": "<b>ذخیره</b>.",
    },
    "07-background": {
        "#cap": "<b>عکس‌برداری از همهٔ دوربین‌ها</b>.",
        "#bgs figure .chip": "وضعیت هر دوربین: گرفته شد، چیزی حرکت کرد، یا ناموفق.",
        "#bgs [data-re]": "<b>دوباره</b>: عکس‌برداری دوباره فقط از همین دوربین.",
    },
    "08-colours-empty": {
        "#side .picker": "<b>فهرست توپ‌ها</b>: اول توپ را این‌جا انتخاب کنید.",
        "#view": "سپس روی همان توپ در تصویر کلیک کنید.",
        "#auto": "<b>نمونه‌برداری خودکار</b>: فقط برای دوربین‌های نمایشی.",
    },
    "09-colours-done": {
        "#side .card h3": "شمار توپ‌هایی که این دوربین یاد گرفته.",
        "#side .pick.on": "توپ انتخاب‌شده؛ تیک سبز یعنی رنگش یاد گرفته شده.",
    },
    "10-output": {
        "#ven": "<b>پر کردن تایتل vMix</b>: روشن کنید.",
        "#vhost": "<b>رایانهٔ vMix</b>: اگر vMix روی همین رایانه است 127.0.0.1.",
        "#vinput": "<b>ورودی تایتل</b>: نام تایتل جدول در vMix.",
        "#vname": "<b>فیلد نام</b>: الگوی نام فیلدها، {n} شمارهٔ ردیف است.",
        "#vinfo": "<b>فیلد زمان / فاصله</b>.",
        "#vclock": "<b>فیلدهای ساعت و وضعیت</b>.",
        "#vrows": "<b>تعداد ردیف‌های تایتل</b>: 0 یعنی یک ردیف برای هر توپ.",
        "#vtest": "<b>آزمایش vMix</b>: اتصال، وجود تایتل و همهٔ فیلدها را بررسی می‌کند.",
    },
    "10b-output-vmix-test": {
        "#vtest": "<b>آزمایش vMix</b>.",
        "#vres": "نتیجهٔ آزمایش، با توضیح مشکل اگر وصل نشد.",
    },
    "11a-review-incomplete": {
        ".checklist .check button": "<b>رفع</b>: شما را مستقیم به مرحله‌ای می‌برد که کار دارد.",
        "#foot .msg": "اولین کاری که مانده.",
        "#golive": "<b>شروع زنده</b>: تا همه چیز کامل نشود غیرفعال است.",
    },
    "11-review": {
        ".checklist": "فهرست بازبینی: همه سبز.",
        "#checkViews": "<b>بررسی نمای دوربین‌ها</b>: مقایسه با تصویرهای ذخیره‌شده در نقشه.",
        "#golive": "<b>شروع زنده</b>.",
    },
    "11b-review-views": {
        "#checkViews": "<b>بررسی نمای دوربین‌ها</b>؛ نتیجهٔ هر دوربین زیر آن فهرست می‌شود.",
    },
    "12-live-race": {
        "#rstate": "<b>وضعیت مسابقه</b>: در انتظار، در حال مسابقه، موقت، نهایی.",
        "#clock": "<b>ساعت مسابقه</b>.",
        "#newrace": "<b>مسابقهٔ جدید</b>: جدول را پاک می‌کند و منتظر مسابقهٔ بعد می‌ماند.",
        "#stop": "<b>بازگشت به راه‌اندازی</b>: ردیابی را متوقف می‌کند.",
        "#confirmres": "<b>تأیید نتیجه</b>: بعد از پایان مسابقه فعال می‌شود.",
        "#freeze": "<b>ثابت کردن جدول</b> روی آنتن.",
        "#hideb": "<b>پنهان کردن جدول</b> از آنتن.",
        "#rows": "<b>جدول زنده</b>: رتبه، رنگ، نام، فاصله یا زمان پایان. برای اصلاح روی هر ردیف کلیک کنید.",
        "#tiles": "<b>تصویر کوچک دوربین‌ها</b>: با کلیک، دوربین در قاب بزرگ نشان داده می‌شود.",
        "#focus": "قاب بزرگ دوربین انتخاب‌شده.",
        ".status-bar .sbox:nth-child(1)": "<b>دوربین‌ها</b>: چند دوربین آنلاین است و سرعت پردازش.",
        ".status-bar .sbox:nth-child(4)": "<b>کنترل مسابقه</b>: گزارش شروع، پایان‌ها، مشکل دوربین‌ها و اصلاح‌های اپراتور.",
    },
    "13-live-finished": {
        "#rstate": "مسابقه تمام شده و نتیجه <b>موقت</b> است.",
        "#confirmres": "<b>تأیید نتیجه</b>: پس از نگاه به قفسهٔ پایان بزنید؛ نتیجه «نهایی» می‌شود.",
        "#rows .lrow.photo": "<b>فوتوفینیش: بازبینی شود</b>: زمان‌های خیلی نزدیک؛ با دقت نگاه کنید.",
    },
    "14-live-popover": {
        "#pop select": "توپی که ردیاب این توپ را با آن قاطی کرده.",
        "#pop [data-swap]": "<b>جابه‌جا کردن این دو</b>.",
        "#pop [data-mv='-1']": "<b>یک رتبه بالاتر</b> (فقط پس از پایان مسابقه).",
        "#pop [data-mv='1']": "<b>یک رتبه پایین‌تر</b> (فقط پس از پایان مسابقه).",
    },
    "16-live-training": {
        "#hlabel": "توپی که تنها رها می‌کنید (یا «بدون توپ»).",
        "#harvest": "<b>شروع</b> / <b>توقف</b> جمع‌آوری تصویرها.",
    },
}


def _marker_pos(box, taken=()):
    """Where a number badge goes: on a corner of a button or field (top right first: the interface is
    right to left, so labels start there but never reach the corner), the first corner that keeps
    clear of the badges already placed; inside the top-right corner of a large area. The element
    itself gets an outline, so the badge never has to cover it."""
    _, x, y, w, h = box
    if h > 0.12 or w > 0.35:
        spots = [(x + w - 0.02, y + 0.035), (x + 0.02, y + 0.035), (x + w - 0.02, y + h - 0.035)]
    else:
        spots = [(x + w, y), (x, y), (x + w, y + h), (x, y + h), (x - 0.012, y + h / 2), (x + w + 0.012, y + h / 2)]
    spots = [(min(0.985, max(0.015, px)), min(0.975, max(0.025, py))) for px, py in spots]
    clear = lambda p: all(abs(p[0] - q[0]) > 0.026 or abs(p[1] - q[1]) > 0.042 for q in taken)
    return next((p for p in spots if clear(p)), spots[0])


def figure(name: str, caption: str, boxes: dict) -> str:
    legend = LEGENDS.get(name, {})
    marks, items, taken = [], [], []
    for box in boxes.get(name, []):
        if not box or box[0] not in legend:
            continue
        n = len(items) + 1
        _, x, y, w, h = box
        px, py = _marker_pos(box, taken)
        taken.append((px, py))
        if not (h > 0.12 or w > 0.35):
            marks.append(f'<span class="hl" style="left:{x * 100:.2f}%;top:{y * 100:.2f}%;width:{w * 100:.2f}%;height:{h * 100:.2f}%"></span>')
        marks.append(f'<span class="mk" style="left:{px * 100:.2f}%;top:{py * 100:.2f}%">{str(n).translate(FA_DIGITS)}</span>')
        items.append(f"<li>{legend[box[0]]}</li>")
    leg = f'<ol class="legend">{"".join(items)}</ol>' if items else ""
    return (f'<figure class="shot"><div class="frame"><img src="img/{name}.jpg" alt="">{"".join(marks)}</div>'
            f"<figcaption>{caption}</figcaption>{leg}</figure>")


def build_html() -> Path:
    boxes = json.loads((SRC / "img" / "boxes.json").read_text())
    src = (SRC / "guide-fa.html").read_text(encoding="utf-8")
    src = src.replace("{{FONT}}", FONT.as_uri())
    src = re.sub(r'<shot n="([^"]+)" cap="([^"]*)"\s*/?>(?:</shot>)?',
                 lambda m: figure(m.group(1), html.unescape(m.group(2)), boxes), src)
    out = SRC / "_build.html"
    out.write_text(src, encoding="utf-8")
    return out


def main():
    from playwright.sync_api import sync_playwright

    page_html = build_html()
    with sync_playwright() as p:
        b = p.chromium.launch(**({"executable_path": os.environ["CHROMIUM"]} if os.environ.get("CHROMIUM") else {}))  # CHROMIUM: a browser other than Playwright's own
        pg = b.new_page()
        pg.goto(page_html.as_uri())
        pg.wait_for_load_state("networkidle")
        pg.evaluate("document.fonts.ready")
        pg.pdf(path=str(OUT), format="A4", print_background=True, prefer_css_page_size=True,
               display_header_footer=True, header_template="<span></span>",
               footer_template='<div style="width:100%;text-align:center;font-size:8px;color:#888;font-family:sans-serif">'
                               '<span class="pageNumber"></span> / <span class="totalPages"></span></div>',
               margin={"top": "14mm", "bottom": "16mm", "left": "14mm", "right": "14mm"})
        b.close()
    page_html.unlink()
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
