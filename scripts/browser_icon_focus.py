"""Icon alignment and keyboard-focus regression checks; no LLM requests are sent."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8518")
args = parser.parse_args()
output = Path(__file__).resolve().parents[1] / "artifacts"
output.mkdir(exist_ok=True)
results, failures = [], []

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    for theme, scale in (("dark", 1), ("dark", 1.25), ("dark", 1.5), ("light", 1)):
        context = browser.new_context(
            viewport={"width": 1440, "height": 900}, color_scheme=theme, device_scale_factor=scale
        )
        page = context.new_page()
        page.goto(args.url)
        question = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
        question.wait_for()
        page.wait_for_load_state("networkidle")
        checks = {}
        collapse = page.get_by_role("button", name="Collapse sidebar")
        checks["collapse_centered"] = collapse.evaluate(
            "e=>getComputedStyle(e).justifyContent==='center'"
        )
        checks["collapse_clear_of_wordmark"] = (
            collapse.bounding_box()["x"] + collapse.bounding_box()["width"]
            < page.locator(".workspace-brand").bounding_box()["x"]
        )
        for name, label in (("add", "添加资料"), ("search", "搜索会话")):
            button = page.get_by_role("button", name=label, exact=True)
            checks[f"{name}_centered"] = button.evaluate(
                """e=>{
                    const b=e.getBoundingClientRect();
                    const i=e.querySelector('[data-testid=stIconMaterial]').getBoundingClientRect();
                    return Math.abs(b.x+b.width/2-i.x-i.width/2)<.75 &&
                        Math.abs(b.y+b.height/2-i.y-i.height/2)<.75;
                }"""
            )
            button.hover()
            button.screenshot(path=str(output / f"icon-{name}-{theme}-{scale}.png"))
        collapse.hover()
        collapse.screenshot(path=str(output / f"icon-collapse-{theme}-{scale}.png"))
        # A scrollable main region may own keyboard focus, but must not draw a page-sized ring.
        page.mouse.click(1300, 450)
        page.keyboard.press("a")
        checks["no_page_sized_focus_ring"] = page.locator('[data-testid="stMain"]').evaluate(
            "e=>getComputedStyle(e,'::before').boxShadow==='none'"
        )
        question.click()
        page.mouse.move(1300, 450)
        page.keyboard.type("abc")
        checks["pointer_does_not_steal_input_focus"] = (
            question.input_value() == "abc" and question.evaluate("e=>e===document.activeElement")
        )
        question.fill("")  # Keep Enter from submitting a question during menu acceptance.
        question.press("Tab")
        checks["keyboard_control_focus_retained"] = page.evaluate(
            """()=>{
                const e=document.activeElement, s=getComputedStyle(e);
                return e.tagName==='BUTTON' && e.matches(':focus-visible') &&
                    (s.boxShadow!=='none'||(s.outlineStyle!=='none'&&parseFloat(s.outlineWidth)>0));
            }"""
        )
        for _ in range(8):
            if page.locator(".st-key-composer_add button").evaluate(
                "e=>e===document.activeElement"
            ):
                break
            page.keyboard.press("Tab")
        assert page.locator(".st-key-composer_add button").evaluate("e=>e===document.activeElement")
        page.keyboard.press("Enter")
        menu = page.locator('[data-testid="stPopoverBody"]')
        menu.wait_for()
        page.keyboard.press("Escape")
        menu.wait_for(state="hidden")
        checks["keyboard_menu_round_trip"] = True
        page.set_viewport_size({"width": 1440, "height": 540})
        page.locator('[data-testid="stMain"]').focus()
        page.keyboard.press("PageDown")
        page.wait_for_function("document.querySelector('[data-testid=stMain]').scrollTop>0")
        checks["keyboard_scrolling_retained"] = True
        page.screenshot(path=str(output / f"focus-{theme}-{scale}.png"))
        results.append({"theme": theme, "scale": scale, "checks": checks})
        failures.extend(f"{theme}/{scale}: {name}" for name, passed in checks.items() if not passed)
        context.close()
    browser.close()

print(json.dumps(results, indent=2))
assert not failures, failures
