"""Real Edge acceptance against the explicitly isolated round3_app.py fixture."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8527")
    parser.add_argument("--workspace", type=Path, default=Path("artifacts/review-round3-ui"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    workspace = args.workspace.resolve()
    assert workspace.is_relative_to(root / "artifacts")
    release = workspace / ".release-job"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()
        page.goto(args.url)
        page.get_by_role("button", name="新建会话", exact=True).click()
        prompt = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
        prompt.wait_for()
        assert (workspace / ".review-fixture").read_text() == "round3 browser fixture"
        release.unlink(missing_ok=True)
        prompt.fill("未保存的第一轮")
        prompt.press("Enter")
        warning = page.get_by_text("回答已生成，但未保存到历史记录。", exact=False)
        expect(warning).to_have_count(1)
        prompt.fill("成功的第二轮")
        prompt.press("Enter")
        body = page.locator('[data-testid="stChatMessage"]')
        expect(body).to_have_count(4)
        texts = body.all_inner_texts()
        assert "未保存的第一轮" in texts[0] and "成功的第二轮" in texts[2]
        copy = page.locator('[data-copy-target^="answer_body_unsaved_"]')
        copy.click()
        expect(copy).to_have_attribute("data-state", "copied")
        assert "验收回答：未保存的第一轮" in page.evaluate("navigator.clipboard.readText()")
        page.get_by_role("button", name="移除此未保存记录", exact=True).click()
        expect(warning).to_have_count(0)
        expect(body).to_have_count(2)
        assert "成功的第二轮" in body.first.inner_text()
        prompt.fill("等待后台失败")
        prompt.press("Enter")
        page.get_by_role("button", name="停止生成", exact=True).wait_for()
        page.get_by_role("radio", name="系统设置", exact=True).check(force=True)
        page.get_by_role("heading", name="系统设置", exact=True).wait_for()
        manage = page.get_by_role("button", name="管理当前会话", exact=True)
        manage.click()
        page.get_by_text("确认删除当前会话及其消息", exact=True).click()
        expect(page.get_by_role("checkbox", name="确认删除当前会话及其消息")).to_be_checked()
        expect(page.get_by_role("button", name="删除会话", exact=True)).to_be_disabled()
        expect(page.locator("#qa-jobs")).to_have_attribute("data-count", "1")
        release.write_text("release test job")  # No UI event: completion must be noticed off chat.
        expect(page.locator("#qa-jobs")).to_have_attribute("data-count", "0", timeout=10000)
        if not page.get_by_role("button", name="删除会话", exact=True).is_visible():
            manage.click()
        expect(page.get_by_role("button", name="删除会话", exact=True)).to_be_enabled()
        page.keyboard.press("Escape")
        page.get_by_role("radio", name="AI 聊天", exact=True).check(force=True)
        expect(warning).to_have_count(1)
        expect(body).to_have_count(4)
        texts = body.all_inner_texts()
        assert "成功的第二轮" in texts[0] and "等待后台失败" in texts[2]
        expect(page.locator('[data-copy-target^="answer_body_unsaved_"]')).to_have_count(1)
        assert page.locator('[data-testid="stException"]').count() == 0
        page.screenshot(path=str(root / "artifacts/review-round3-browser.png"), full_page=True)
        browser.close()
    report = {
        "real_edge": True,
        "timeline_failure_then_success": True,
        "clipboard": True,
        "discard_unsaved_only": True,
        "off_chat_completion_without_ui_event": True,
        "delete_unblocked": True,
        "failed_answer_retained_after_page_switch": True,
        "paid_api_calls": 0,
    }
    (root / "artifacts/review-round3-browser.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
