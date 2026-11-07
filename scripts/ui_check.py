#!/usr/bin/env python3
"""Visual/structural UI check for aikeytake-zog.

Usage:
    uv run --with playwright python scripts/ui_check.py <base_url> <password> <out_dir>

Run against a COPY of the data (see docs/aikeytake-zog.md, "UI check").
Playwright is not a project dependency; it is pulled in by `uv run --with`.
Env UI_CHECK_NARROW_WIDTH overrides the 375px width (used to prove the checks can fail).
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

WIDE = 1400
NARROW = int(os.environ.get("UI_CHECK_NARROW_WIDTH", "375"))
RESULTS: list[tuple[bool, str]] = []
ERRORS: list[str] = []


def record(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" - {detail}" if detail else ""))


def overflow(page) -> tuple[bool, str]:
    sw, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    return sw <= iw, f"scrollWidth={sw} innerWidth={iw}"


def settle(page) -> None:
    page.wait_for_load_state("networkidle")


def shot(page, out: Path, name: str, width: int, check_overflow: bool = True) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.wait_for_timeout(100)
    page.screenshot(path=str(out / f"{name}.png"), full_page=True)
    if check_overflow:
        ok, d = overflow(page)
        record(ok, f"no horizontal overflow: {name} ({width}px)", d)


def row_item_href(page, link_selector: str, exclude: str = "") -> str | None:
    """Item href of the first library row containing link_selector (and not `exclude`)."""
    return page.evaluate(
        """([sel, excl]) => {
          for (const start of document.querySelectorAll(sel)) {
            let el = start;
            while (el && el !== document.body) {
              const a = el.querySelector("a[href^='/items/']");
              if (a) {
                if (!excl || !el.querySelector(excl)) return a.getAttribute('href');
                break;
              }
              el = el.parentElement;
            }
          }
          return null;
        }""",
        [link_selector, exclude],
    )


def login(page, base: str, password: str) -> bool:
    page.goto(base + "/login")
    page.fill("input[name=password]", password)
    page.click("button[type=submit], input[type=submit]")
    settle(page)
    return "/login" not in page.url


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    base, password, out_arg = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
    out = Path(out_arg)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": WIDE, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: ERRORS.append(f"console error on {page.url}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: ERRORS.append(f"page error on {page.url}: {e}"))

        if not login(page, base, password):
            record(False, "login", "still on /login (wrong password?)")
            browser.close()
            return summary()
        record(True, "login")

        # Library
        page.goto(base + "/")
        settle(page)
        n_rows = page.locator("a[href^='/items/']").count()
        record(n_rows > 0, "library has item rows", f"{n_rows} item links")
        bulk_visible = page.locator("#bulk").is_visible() if page.locator("#bulk").count() else False
        record(not bulk_visible, "#bulk hidden before selection")
        shot(page, out, "library-1400", WIDE)
        shot(page, out, "library-375", NARROW)
        page.set_viewport_size({"width": WIDE, "height": 900})

        # Nested collection: a <details> in the sidebar with a child link
        child_href = page.evaluate(
            """() => {
              for (const d of document.querySelectorAll('details')) {
                const a = d.querySelector("details a[href*='collection=']");
                if (a) return a.getAttribute('href');
              }
              return null;
            }"""
        )
        if child_href:
            page.goto(base + child_href)
            settle(page)
            shot(page, out, "collection-nested-1400", WIDE)
            shot(page, out, "collection-nested-375", NARROW)
        else:
            print("NOTE: no collection with children found; skipping nested-collection screenshot")

        # PDF item
        page.goto(base + "/")
        settle(page)
        pdf_href = page.locator("a.open-pdf").first.get_attribute("href") if page.locator("a.open-pdf").count() else None
        if pdf_href:
            item = row_item_href(page, "a.open-pdf")
            r = ctx.request.get(base + pdf_href)
            ctype = r.headers.get("content-type", "")
            record(r.status == 200 and ctype.startswith("application/pdf"), "PDF link serves application/pdf",
                   f"{pdf_href} -> {r.status} {ctype}")
            if item:
                page.goto(base + item)
                settle(page)
                shot(page, out, "item-pdf-1400", WIDE)
                shot(page, out, "item-pdf-375", NARROW)
        else:
            print("NOTE: no row with a PDF link found; skipping PDF item checks")

        # Webpage/URL item
        page.goto(base + "/")
        settle(page)
        if page.locator("a.open-url").count():
            item = row_item_href(page, "a.open-url", exclude="a.open-pdf")
            if not item:
                print("NOTE: every URL row also has a PDF; using the first URL row")
                item = row_item_href(page, "a.open-url")
            if item:
                page.goto(base + item)
                settle(page)
                shot(page, out, "item-url-1400", WIDE)
                shot(page, out, "item-url-375", NARROW)
        else:
            print("NOTE: no row with a URL link found; skipping URL item screenshot")

        # History
        page.goto(base + "/history")
        settle(page)
        shot(page, out, "history-1400", WIDE)
        shot(page, out, "history-375", NARROW)

        # Dark scheme, overflow only
        dctx = browser.new_context(viewport={"width": WIDE, "height": 900}, color_scheme="dark")
        dpage = dctx.new_page()
        dpage.on("console", lambda m: ERRORS.append(f"console error (dark) on {dpage.url}: {m.text}") if m.type == "error" else None)
        dpage.on("pageerror", lambda e: ERRORS.append(f"page error (dark) on {dpage.url}: {e}"))
        if login(dpage, base, password):
            for w in (WIDE, NARROW):
                dpage.set_viewport_size({"width": w, "height": 900})
                dpage.goto(base + "/")
                settle(dpage)
                ok, d = overflow(dpage)
                record(ok, f"no horizontal overflow: library dark ({w}px)", d)
            dpage.screenshot(path=str(out / "library-dark.png"), full_page=True)
        else:
            record(False, "dark login")
        browser.close()

    record(not ERRORS, "no console errors or page errors", "; ".join(ERRORS[:5]))
    return summary()


def summary() -> int:
    failed = [n for ok, n in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED: " + "; ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
