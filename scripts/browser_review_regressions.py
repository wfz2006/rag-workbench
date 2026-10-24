"""Real Edge acceptance for the review; use an isolated workspace with no API key."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8525")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
checks = []
with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto(args.url)
    page.get_by_role("button", name="新建会话", exact=True).wait_for()
    for label in ("知识库管理", "系统设置", "检索调试"):
        page.get_by_role("radio", name=label, exact=True).check(force=True)
        page.get_by_role("heading", name=label, exact=True).wait_for()
        search = page.get_by_role("button", name="搜索会话", exact=True)
        search.click()
        page.locator('[data-testid="stPopoverBody"]').wait_for()
        page.keyboard.press("Escape")
        expect(page.locator('[data-testid="stPopoverBody"]')).to_have_count(0)
        search.click()
        page.get_by_role("button", name="管理当前会话", exact=True).click()
        page.get_by_role("textbox", name="会话名称", exact=True).wait_for()
        page.keyboard.press("Escape")
        search.click()
        heading = page.get_by_role("heading", name=label, exact=True).bounding_box()
        page.mouse.move(heading["x"] + 100, heading["y"] + 10)
        page.mouse.down()
        page.wait_for_timeout(250)
        page.mouse.up()
        expect(page.locator('[data-testid="stPopoverBody"]')).to_have_count(0)
        checks.append({"page": label, "escape_outside_and_switch": True})
    page.get_by_role("radio", name="系统设置", exact=True).check(force=True)
    expect(page.get_by_role("spinbutton", name="Top-K", exact=True)).to_be_disabled()
    checks.append({"environment_locked_control": True})
    page.get_by_role("radio", name="AI 聊天", exact=True).check(force=True)
    raw = "#include <iostream>\nvector<int> priority_queue<Node>\na < b && c > d\na * b * c"
    prompt = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
    prompt.fill(raw)
    prompt.press("Enter")
    page.get_by_text("尚未配置 DEEPSEEK_API_KEY。", exact=False).wait_for()
    user = (
        page.locator('[data-testid="stChatMessage"]')
        .filter(has=page.locator('[data-testid="stChatMessageAvatarUser"]'))
        .first
    )
    expect(user.locator('[data-testid="stText"]')).to_have_text(raw)
    assert user.locator("h1").count() == 0
    checks.append({"literal_cpp_user_text": True})
    page.screenshot(path=str(root / "artifacts/review-browser.png"), full_page=True)
    browser.close()
(root / "artifacts/review-browser.json").write_text(
    json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(checks, ensure_ascii=False))
