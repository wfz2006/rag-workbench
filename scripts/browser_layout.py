"""Real-browser layout checks. Run only against an isolated QA workspace without an API key."""

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
    for width, height in ((1280, 720), (390, 720), (1280, 540)):
        context = browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()
        page.goto(args.url)
        page.wait_for_load_state("networkidle")
        new_chat = page.get_by_role("button", name="新建会话", exact=True)
        new_chat.wait_for(state="attached")
        if new_chat.bounding_box()["x"] < 0:
            page.get_by_role("button", name="Expand sidebar").click()
        new_chat.click()
        question = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
        question.wait_for()
        if width < 640:
            page.get_by_role("button", name="Collapse sidebar").click()
            page.wait_for_function(
                "document.querySelector('[data-testid=stSidebar]')"
                ".getBoundingClientRect().right <= 1"
            )
        prompt = page.locator(".st-key-prompt_surface")
        bounds = prompt.bounding_box()
        assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
        assert bounds["y"] + bounds["height"] < height
        question.fill("请检查长文本的布局。\n" * 65)
        question.press("End")
        page.get_by_role("button", name="Send message").wait_for()
        assert page.get_by_role("button", name="Send message").is_enabled()
        send_bounds = page.get_by_role("button", name="Send message").bounding_box()
        prompt_bounds = prompt.bounding_box()
        assert send_bounds["y"] + send_bounds["height"] <= (
            prompt_bounds["y"] + prompt_bounds["height"]
        )
        page.screenshot(path=str(output / f"input-expanded-{width}-{height}.png"))
        question.fill("滚动布局检查。" * 110)
        question.press("Enter")
        page.get_by_text("尚未配置 DEEPSEEK_API_KEY。", exact=False).wait_for()
        main = page.locator('[data-testid="stMain"]')
        main.evaluate("el => el.scrollTo(0, el.scrollHeight)")
        page.wait_for_function("document.querySelector('[data-testid=stMain]').scrollTop > 0")
        alert = page.get_by_text("尚未配置 DEEPSEEK_API_KEY。", exact=False).bounding_box()
        assert alert["y"] + alert["height"] < prompt.bounding_box()["y"]
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        page.screenshot(path=str(output / f"conversation-scrolled-{width}-{height}.png"))
        results.append({"width": width, "height": height, "expanded_and_scrolled": "passed"})
        context.close()
    browser.close()

print(json.dumps(results, indent=2))
