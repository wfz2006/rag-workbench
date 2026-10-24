"""Screenshot-led geometry acceptance. Use only the isolated local SSE QA server."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8519")
args = parser.parse_args()
output = Path(__file__).resolve().parents[1] / "artifacts"
results = []

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    context = browser.new_context(
        viewport={"width": 1048, "height": 696}, device_scale_factor=1.5, color_scheme="dark"
    )
    page = context.new_page()
    page.set_default_timeout(60000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(args.url)
    page.get_by_role("button", name="新建会话", exact=True).click()
    page.get_by_text("今天，想弄清楚什么？", exact=True).wait_for()
    question = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
    question.fill("long")
    question.press("Enter")
    page.get_by_role("button", name="复制回答", exact=True).wait_for()
    main = page.locator('[data-testid="stMain"]')
    for expanded in (True, False, True):
        if not expanded:
            page.get_by_role("button", name="Collapse sidebar").click()
        elif results:
            page.get_by_role("button", name="Expand sidebar").click()
        expected_left = 286 if expanded else 0
        page.wait_for_function(
            "x => document.querySelector('[data-testid=stMain]').getBoundingClientRect().x === x",
            arg=expected_left,
        )
        prompt = page.locator(".st-key-prompt_surface").bounding_box()
        controls = page.locator(".st-key-composer_controls").bounding_box()
        assert abs(prompt["x"] + prompt["width"] / 2 - (1048 + expected_left) / 2) < 1
        assert prompt["height"] <= 52
        assert controls["y"] >= prompt["y"] + prompt["height"]
        assert controls["y"] + controls["height"] <= 696
        if expanded:
            sidebar = page.locator('[data-testid="stSidebarContent"]').bounding_box()
            assert sidebar["x"] == 0 and sidebar["y"] == 0 and sidebar["height"] == 696
            assert (
                page.locator('[data-testid="stSidebarContent"]').evaluate(
                    "e=>getComputedStyle(e).borderRadius"
                )
                == "0px"
            )
        else:
            assert page.get_by_role("button", name="Expand sidebar").is_visible()
        main.evaluate("e=>e.scrollTo({top:0,behavior:'instant'})")
        page.wait_for_function("document.querySelector('[data-testid=stMain]').scrollTop===0")
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        page.mouse.move(1030, 100)
        page.screenshot(
            path=str(output / f"reference-{'expanded' if expanded else 'collapsed'}.png")
        )
        results.append({"expanded": expanded, "input": prompt, "geometry": "passed"})
    page.get_by_role("button", name="搜索会话", exact=True).click()
    page.get_by_role("textbox", name="搜索会话", exact=True).fill("long")
    page.keyboard.press("Escape")
    page.locator('[data-testid="stPopoverBody"]').wait_for(state="hidden")
    assert not errors, errors
    context.close()
    browser.close()

print(json.dumps(results, indent=2))
