"""Screenshots for the user guide (docs/guide/img), from the app in demo mode, Persian interface.

Start the app with a fresh data folder, then run this against its URL (printed at start):

    python -m marbletrack_app --demo --no-open --port 8790 --home /tmp/mt-guide
    python scripts/guide_screenshots.py setup "http://127.0.0.1:8790/?k=..." fa out/      # every setup step
    # fresh app again, then the live screen with eight cameras (fewer camera processes):
    python scripts/guide_screenshots.py live "http://127.0.0.1:8790/?k=..." out/live/

Each run also writes boxes.json: where every marked button is, for the numbered markers.
Pick the images into docs/guide/img (1800 px JPEG) and run scripts/build_guide.py.
"""
import os
import sys


def setup():
    from playwright.sync_api import sync_playwright
    import json
    URL, LANG, OUT = sys.argv[2], sys.argv[3], sys.argv[4]
    LIVE_WAIT = int(sys.argv[5]) if len(sys.argv) > 5 else 240
    CHECK = """() => { const out = new Set(); const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (w.nextNode()) { const n = w.currentNode, p = n.parentElement; if (!p || p.closest('svg,code,pre,script,style,[data-noi18n]')) continue;
        const r = p.getBoundingClientRect(); if (!r.width || getComputedStyle(p).visibility === 'hidden' || p.closest('[hidden]')) continue;
        const t = n.nodeValue.trim(); if (/[A-Za-z]{3,}/.test(t)) out.add(t.slice(0, 140)); }
      document.querySelectorAll('[title],[placeholder]').forEach((e) => { for (const a of ['title','placeholder']) { const v = e.getAttribute(a); if (v && /[A-Za-z]{3,}/.test(v) && !/[\\u0600-\\u06FF]/.test(v)) out.add('@' + a + ': ' + v.slice(0, 140)); } });
      return [...out]; }"""
    seen = set()
    MARKS = {
      "00-first-run": ["#mapsBtn", "#demoAll", "#guideBtn", "#langBtn", "#modePill", "#rail .step.on", "#foot .msg", "#next"],
      "01-race": ["#pname", "#plen", "#pfin", "#plaps", "#pspc", "details.more summary", "#madd", "#mlist .marble-row"],
      "02-race-more": ["#pstart", "#pchan", "#pdia", "#pblind", "#pwide", "#pauto", "#prack"],
      "03-maps": ["#mname", "#mnotes", "#mexport", "#nname", "#nempty", "#mimport", ".maprow [data-copy]", ".maprow [data-export]"],
      "04-cameras": ["#find", ".cam .order", ".cam .name", ".cam .cam-meta", ".cam .toggle", ".cam [data-mv='1']", ".cam select"],
      "05-waypoints-empty": ["#tabs", "#view", "#side .note"],
      "06-waypoints-placed": ["#tabs .tab-btn.on", ".viewer .lbl", "#side .kv", "#wscale", "#undo", "#stretch", "#clear", "#auto"],
      "06b-waypoint-popover": ["#pop input", "#pop [data-ok]"],
      "07-background": ["#cap", "#bgs figure .chip", "#bgs [data-re]"],
      "08-colours-empty": ["#side .picker", "#view", "#auto"],
      "09-colours-done": ["#side .card h3", "#side .pick.on", "#side .note.ok"],
      "10-output": ["#ven", "#vhost", "#vinput", "#vname", "#vinfo", "#vclock", "#vrows", "#vtest"],
      "10b-output-vmix-test": ["#vtest", "#vres"],
      "11a-review-incomplete": [".checklist .check button", "#foot .msg", "#golive"],
      "11-review": [".checklist", "#checkViews", "#golive"],
      "11b-review-views": ["#checkViews", ".card .checklist"],
      "12-live-race": ["#rstate", "#clock", "#newrace", "#stop", "#confirmres", "#freeze", "#hideb", "#rows", "#tiles", "#focus", ".status-bar .sbox:nth-child(1)", ".status-bar .sbox:nth-child(4)"],
      "13-live-finished": ["#rstate", "#confirmres", "#rows .lrow.photo"],
      "14-live-popover": ["#pop select", "#pop [data-swap]", "#pop [data-mv='-1']", "#pop [data-mv='1']"],
      "15-live-confirmed": ["#rstate", "#confirmres"],
      "16-live-training": ["#hlabel", "#harvest"],
    }
    BOXES = {}
    RECT = """(sels) => sels.map((s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect();
      if (!r.width || r.bottom < 0 || r.top > innerHeight) return null;
      return [s, r.left / innerWidth, r.top / innerHeight, r.width / innerWidth, r.height / innerHeight]; })"""
    with sync_playwright() as p:
        b = p.chromium.launch(**({"executable_path": os.environ["CHROMIUM"]} if os.environ.get("CHROMIUM") else {}))  # CHROMIUM: a browser other than Playwright's own
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5)
        ctx.add_init_script(f"try {{ localStorage.setItem('marbletrack.lang', '{LANG}') }} catch {{}}")
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: print("ERR", e, flush=True))
        def shot(name, wait=900, **kw):
            pg.wait_for_timeout(wait)
            if name in MARKS: BOXES[name] = pg.evaluate(RECT, MARKS[name])
            pg.screenshot(path=f"{OUT}/{name}.png", **kw)
            if LANG == "fa":
                new = [t for t in pg.evaluate(CHECK) if t not in seen]; seen.update(new)
                if new: print(f"[{name}] untranslated:", json.dumps(new, ensure_ascii=False), flush=True)
            print("shot", name, flush=True)
        def step(sid):
            pg.click(f'#rail [data-step="{sid}"]'); pg.wait_for_timeout(1200)
        pg.goto(URL); pg.wait_for_timeout(2500)
        shot("00-first-run")
        pg.fill("#pname", "Saturday cup"); pg.press("#pname", "Tab")
        shot("01-race")
        pg.click("details.more summary"); shot("02-race-more")
        pg.click("#mapsBtn"); shot("03-maps"); pg.click("#mclose")
        step("cameras"); pg.wait_for_selector(".cam", timeout=60000); shot("04-cameras", wait=3000)
        step("waypoints"); shot("05-waypoints-empty", wait=2500)
        pg.click("#auto"); pg.wait_for_timeout(4000); shot("06-waypoints-placed", wait=2500)
        pg.click(".viewer .lbl >> nth=2"); shot("06b-waypoint-popover", wait=600); pg.keyboard.press("Escape")
        step("background"); pg.click("#cap"); pg.wait_for_timeout(8000); shot("07-background", wait=2000)
        step("colours"); shot("08-colours-empty", wait=2500)
        pg.click("#auto"); pg.wait_for_timeout(8000); shot("09-colours-done", wait=2500)
        step("server"); shot("10-output")
        pg.click("#ven"); pg.wait_for_timeout(500); pg.click("#vtest"); shot("10b-output-vmix-test", wait=4000)
        pg.click("#ven"); pg.wait_for_timeout(500)
        step("review"); shot("11a-review-incomplete", wait=2000)
        pg.click("#demoAll"); pg.wait_for_timeout(70000)
        step("review"); shot("11-review", wait=2500)
        pg.click("#checkViews"); shot("11b-review-views", wait=8000)
        pg.click("#golive"); pg.wait_for_timeout(8000); shot("12-live", wait=1000)
        for k in range(40):
            st = pg.text_content("#rstate") or ""
            if "مسابقه" in st and "جدید" not in st: break
            pg.wait_for_timeout(1500)
        pg.wait_for_timeout(5000); shot("12-live-race", wait=0)
        for k in range(LIVE_WAIT // 3):
            st = pg.text_content("#rstate") or ""
            if any(x in st for x in ("Provisional", "Final", "موقت", "نهایی")): break
            if k % 3 == 0: shot(f"12-live-{k:02d}", wait=0)
            pg.wait_for_timeout(3000)
        shot("13-live-finished", wait=1500)
        pg.click("#rows .lrow:nth-child(2)"); shot("14-live-popover", wait=800)
        pg.mouse.click(1100, 600); pg.wait_for_timeout(600)
        print("confirm enabled:", pg.is_enabled("#confirmres"), pg.text_content("#rstate"), flush=True)
        pg.click("#confirmres", force=True); shot("15-live-confirmed", wait=1500)
        pg.click("#live details.more summary"); shot("16-live-training", wait=800)
        pg.click("#stop"); pg.wait_for_timeout(4000); shot("17-back-to-setup", wait=1000)
        open(f"{OUT}/boxes.json", "w").write(json.dumps(BOXES, indent=1))
        b.close()


def live():
    from playwright.sync_api import sync_playwright
    import json
    URL, OUT = sys.argv[2], sys.argv[3]
    MARKS = ["#rstate", "#clock", "#newrace", "#stop", "#confirmres", "#freeze", "#hideb", "#rows", "#tiles", "#focus", ".status-bar .sbox:nth-child(1)", ".status-bar .sbox:nth-child(4)"]
    RECT = """(sels) => sels.map((s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect();
      if (!r.width || r.bottom < 0 || r.top > innerHeight) return null; return [s, r.left / innerWidth, r.top / innerHeight, r.width / innerWidth, r.height / innerHeight]; })"""
    boxes = {}
    with sync_playwright() as p:
        b = p.chromium.launch(**({"executable_path": os.environ["CHROMIUM"]} if os.environ.get("CHROMIUM") else {}))  # CHROMIUM: a browser other than Playwright's own
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5)
        ctx.add_init_script("try { localStorage.setItem('marbletrack.lang', 'fa') } catch {}")
        pg = ctx.new_page(); pg.on("pageerror", lambda e: print("ERR", e, flush=True))
        pg.goto(URL); pg.wait_for_timeout(2500)
        pg.click("#demoAll"); pg.wait_for_timeout(75000)
        # keep the start half of the track and the finish camera: fewer camera processes on this small machine
        pg.evaluate("""async () => { for (const c of S.st.cameras) if (/cam(0[8-9]|1[0-4])$/.test(c.key)) await api('update_camera', { key: c.key, enabled: false }); }""")
        pg.wait_for_timeout(2000)
        pg.click('#rail [data-step="review"]'); pg.wait_for_timeout(1500)
        pg.click("#golive"); pg.wait_for_timeout(6000)
        pg.click("#tiles .tile:nth-child(3)"); pg.wait_for_timeout(1000)
        for k in range(90):
            pg.wait_for_timeout(1000)
            st = pg.text_content("#rstate") or ""
            pg.screenshot(path=f"{OUT}/live-{k:02d}.png"); boxes[f"live-{k:02d}"] = pg.evaluate(RECT, MARKS)
            print(k, st, flush=True)
            if "موقت" in st: break
        pg.wait_for_timeout(1500); pg.screenshot(path=f"{OUT}/finished.png"); boxes["finished"] = pg.evaluate(RECT, MARKS)
        pg.click("#confirmres"); pg.wait_for_timeout(1500); pg.screenshot(path=f"{OUT}/confirmed.png"); boxes["confirmed"] = pg.evaluate(RECT, MARKS)
        open(f"{OUT}/boxes.json", "w").write(json.dumps(boxes))
        b.close()


if __name__ == "__main__":
    {"setup": setup, "live": live}[sys.argv[1]]()
