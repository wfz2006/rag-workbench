"""Run against tests/browser_support/persistence_app.py, never a user workspace."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8526")
    parser.add_argument("--output", type=Path, default=Path("artifacts/review-round2-browser.json"))
    args = parser.parse_args()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()
        page.goto(args.url)
        prompt = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
        prompt.wait_for()
        first_id = page.locator(".interaction-scope").get_attribute("data-session")
        prompt.fill("第一条未保存问题")
        prompt.press("Enter")
        warning = page.get_by_text("回答已生成，但未保存到历史记录。", exact=False)
        expect(warning).to_have_count(1)
        copy = page.get_by_role("button", name="复制回答", exact=True)
        page.mouse.move(0, 0)
        expect(copy).to_have_css("opacity", "1")
        copy.click()
        expect(page.locator('[data-copy-target][data-state="copied"]')).to_be_visible()
        clipboard = page.evaluate("navigator.clipboard.readText()")
        assert "无法确认" in clipboard and "通用模式" not in clipboard
        prompt.fill("第二条未保存问题")
        prompt.press("Enter")
        expect(warning).to_have_count(2)
        expect(page.locator("[data-copy-target]")).to_have_count(2)
        page.get_by_role("button", name="新建会话", exact=True).click()
        expect(warning).to_have_count(0)
        expect(page.locator(".interaction-scope")).not_to_have_attribute("data-session", first_id)
        new_id = page.locator(".interaction-scope").get_attribute("data-session")
        page.get_by_role("radio", name="知识库管理", exact=True).check(force=True)
        page.get_by_role("heading", name="知识库管理", exact=True).wait_for()
        previous = page.locator(f".st-key-session_{first_id} button:visible")
        expect(previous).to_have_count(1)
        previous.click()
        expect(warning).to_have_count(2)
        expect(page.locator("[data-copy-target]")).to_have_count(2)
        assert page.locator(".interaction-scope").get_attribute("data-session") == first_id
        assert page.locator('[data-testid="stException"]').count() == 0
        page.screenshot(path=str(args.output.with_suffix(".png")), full_page=True)
        browser.close()
    report = {
        "actual_edge_browser": True,
        "clipboard_contains_generated_answer": True,
        "multiple_unsaved_answers_preserved": True,
        "new_session_excludes_unsaved": first_id != new_id,
        "return_from_other_page_restores_answers_and_copy": True,
        "paid_api_requests": 0,
    }
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
