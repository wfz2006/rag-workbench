"""Optional real-browser acceptance. Requires Playwright and local Edge, outside core dependencies.

Usage: python scripts/browser_smoke.py --url http://127.0.0.1:8518
Run against an isolated QA workspace, never a private or production workspace.
"""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8518")
args = parser.parse_args()
output = Path(__file__).resolve().parents[1] / "artifacts"
output.mkdir(exist_ok=True)
results = []

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    for scheme, width in (("light", 1440), ("dark", 1440), ("light", 390), ("dark", 390)):
        context = browser.new_context(
            viewport={"width": width, "height": 1000}, color_scheme=scheme
        )
        page = context.new_page()
        page.goto(args.url)
        page.get_by_role("button", name="新建会话", exact=True).wait_for(state="attached")
        new_chat_box = page.get_by_role("button", name="新建会话", exact=True).bounding_box()
        if new_chat_box is None or new_chat_box["x"] < 0:
            page.get_by_role("button", name="Expand sidebar").click()
            page.wait_for_function(
                "document.querySelector('[data-testid=stSidebar]').getBoundingClientRect().x >= 0"
            )
        page.get_by_role("button", name="新建会话", exact=True).click()
        page.get_by_text("今天，想弄清楚什么？", exact=True).wait_for()
        page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…").wait_for()
        page.get_by_text("AI 聊天", exact=True).wait_for()
        if width < 640 and page.get_by_role("button", name="Collapse sidebar").is_visible():
            page.get_by_role("button", name="Collapse sidebar").click()
            page.wait_for_function(
                "document.querySelector('[data-testid=stSidebar]')"
                ".getBoundingClientRect().right <= 1"
            )
        if scheme == "dark":
            page.get_by_role("button", name="Main menu").click()
            page.get_by_role("menuitemradio", name="Dark", exact=True).click()
            page.get_by_role("button", name="Main menu").click()
        background = page.locator('[data-testid="stApp"]').evaluate(
            "el => getComputedStyle(el).backgroundColor"
        )
        overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        assert not overflow, f"Horizontal overflow at {width}px"
        assert background == ("rgb(20, 20, 20)" if scheme == "dark" else "rgb(250, 249, 246)")
        page.mouse.move(width - 20, 150)
        page.screenshot(path=str(output / f"chat-{scheme}-{width}.png"), full_page=True)
        results.append(
            {
                "scheme": scheme,
                "width": width,
                "background": background,
                "horizontal_overflow": overflow,
            }
        )
        if width == 1440 and scheme == "light":
            page.get_by_role("button", name="Main menu").click()
            assert page.get_by_role("menuitemradio", name="Dark", exact=True).is_visible()
            page.get_by_role("button", name="Main menu").click()
            for label, title in (
                ("知识库管理", "知识库管理"),
                ("系统设置", "系统设置"),
                ("检索调试", "检索调试"),
            ):
                page.get_by_role("radio", name=label, exact=True).check(force=True)
                page.locator('[data-testid="stMain"]').get_by_text(title, exact=True).wait_for()
                ready = {
                    "知识库管理": "导入并建立索引",
                    "系统设置": "保存设置",
                    "检索调试": "运行检索与证据判断",
                }[label]
                page.get_by_role("button", name=ready, exact=True).wait_for()
                page.screenshot(path=str(output / f"page-{label}.png"), full_page=True)
            results.append({"desktop_navigation": "passed"})
            page.get_by_role("radio", name="AI 聊天", exact=True).check(force=True)
            page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…").wait_for()
            page.get_by_role("button", name="添加资料", exact=True).click()
            page.get_by_role("button", name="上传或管理文档", exact=True).click()
            page.get_by_role("button", name="导入并建立索引", exact=True).wait_for()
            page.get_by_role("button", name="新建会话", exact=True).click()
            question = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
            question.fill("你好")
            question.press("Enter")
            page.get_by_text("尚未配置 DEEPSEEK_API_KEY。", exact=False).wait_for()
            page.screenshot(path=str(output / "conversation-service-error.png"), full_page=True)
            results.append({"composer_navigation_and_service_error": "passed"})
        context.close()
    browser.close()

print(json.dumps(results, indent=2))
