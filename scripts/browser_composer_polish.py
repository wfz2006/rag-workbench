"""Composer, welcome layout, and menu-theme checks against an isolated QA workspace."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8518")
args = parser.parse_args()
output = Path(__file__).resolve().parents[1] / "artifacts"
results, failures = [], []

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    for width, height, theme in ((1440, 900, "dark"), (1280, 720, "dark"), (390, 760, "light")):
        context = browser.new_context(
            viewport={"width": width, "height": height}, color_scheme=theme
        )
        page = context.new_page()
        page.goto(args.url)
        new_chat = page.get_by_role("button", name="新建会话", exact=True)
        new_chat.wait_for(state="attached")
        if new_chat.bounding_box()["x"] < 0:
            page.get_by_role("button", name="Expand sidebar").click()
        new_chat.click()
        page.get_by_text("今天，想弄清楚什么？", exact=True).wait_for()
        question = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
        question.wait_for()
        page.wait_for_function("!!window.__ragWorkbenchInteraction")
        if width <= 1280 and page.get_by_role("button", name="Collapse sidebar").is_visible():
            page.get_by_role("button", name="Collapse sidebar").click()
            page.wait_for_function(
                "document.querySelector('[data-testid=stMain]').getBoundingClientRect().x===0"
            )
        checks = {}
        checks["return_glyph_size"] = page.get_by_role("button", name="Send message").evaluate(
            "e=>parseFloat(getComputedStyle(e,'::after').width)>=24"
        )
        checks["welcome_unified_and_centered"] = page.evaluate(
            """()=>{
                const p=document.querySelector('.st-key-welcome_panel');
                const c=document.querySelector('.st-key-composer');
                if(!p||!c)return false;
                const a=p.getBoundingClientRect(),b=c.getBoundingClientRect();
                return Math.abs(a.x+a.width/2-b.x-b.width/2)<2;
            }"""
        )
        checks["welcome_actions_centered"] = page.locator(".st-key-chat_intro_actions").evaluate(
            """e=>{
                const children=[...e.children].map(c=>c.getBoundingClientRect());
                const r=e.getBoundingClientRect();
                return Math.abs((children[0].left+children.at(-1).right)/2-(r.x+r.width/2))<2;
            }"""
        )
        for name, label in (("add", "添加资料"), ("model", "deepseek-flash")):
            page.get_by_role("button", name=label, exact=True).click()
            menu = page.locator('[data-testid="stPopoverBody"]')
            menu.wait_for()
            expected = "rgb(48, 48, 47)" if theme == "dark" else "rgb(253, 252, 249)"
            checks[f"{name}_menu_surface"] = (
                menu.evaluate("e=>getComputedStyle(e).backgroundColor") == expected
            )
            if name == "add":
                checks["menu_labels_left_aligned"] = menu.get_by_role(
                    "button", name="上传或管理文档", exact=True
                ).evaluate(
                    "e=>getComputedStyle(e.querySelector('[data-has-shortcut]')).justifyContent==='flex-start'"
                )
                menu.get_by_role("button").first.hover()
            menu.evaluate(
                "e=>Promise.all(e.getAnimations({subtree:true}).map(a=>a.finished.catch(()=>{})))"
            )
            header = (
                menu.locator('[data-testid="stCaptionContainer"]')
                if name == "add"
                else menu.locator(".model-menu-summary")
            )
            heading_bounds = header.bounding_box()
            button_bounds = menu.get_by_role("button").first.bounding_box()
            checks[f"{name}_heading_not_covered"] = (
                heading_bounds["y"] + heading_bounds["height"] + 2 <= button_bounds["y"]
            )
            page.screenshot(path=str(output / f"composer-menu-{name}-{width}-{theme}.png"))
            page.keyboard.press("Escape")
            menu.wait_for(state="hidden")
        # Switching the app theme must also switch the portal outside stApp.
        page.get_by_role("button", name="Main menu").click()
        opposite = "Light" if theme == "dark" else "Dark"
        page.get_by_role("menuitemradio", name=opposite, exact=True).click()
        page.get_by_role("button", name="Main menu").click()
        page.get_by_role("button", name="添加资料", exact=True).click()
        menu = page.locator('[data-testid="stPopoverBody"]')
        menu.wait_for()
        other_surface = "rgb(253, 252, 249)" if theme == "dark" else "rgb(48, 48, 47)"
        checks["menu_follows_manual_theme"] = (
            menu.evaluate("e=>getComputedStyle(e).backgroundColor") == other_surface
        )
        page.keyboard.press("Escape")
        assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
        results.append({"width": width, "theme": theme, "checks": checks})
        failures.extend(f"{width}/{theme}: {key}" for key, passed in checks.items() if not passed)
        context.close()
    browser.close()

print(json.dumps(results, indent=2))
assert not failures, failures
