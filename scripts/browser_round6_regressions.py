"""Screenshot-led Edge regression checks against the isolated round6 test fixture."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8533")
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    workspace = args.workspace.resolve()
    assert workspace.is_relative_to(root / "artifacts")
    checks = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(
            viewport={"width": 1440, "height": 900},
            permissions=["clipboard-read", "clipboard-write"],
        )
        page.set_default_timeout(12000)
        page.goto(args.url)
        prompt = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
        prompt.wait_for()
        assert (workspace / ".review-fixture").read_text() == "round6"

        def ask(text, failure=False):
            prompt.fill(text)
            prompt.press("Enter")
            if failure:
                page.get_by_role("button", name="重新编辑问题", exact=True).wait_for()
            else:
                page.get_by_text("已回答：" + text, exact=True).wait_for()

        def nav(name):
            page.get_by_role("radio", name=name, exact=True).check(force=True)
            expect(page.locator(".interaction-scope")).to_have_attribute("data-page", name)

        ask("第一轮")
        ask("故障第二轮", True)
        ask("第三轮")
        page.get_by_role("button", name="重新编辑问题", exact=True).click()
        expect(page.get_by_role("button", name="取消编辑", exact=True)).to_be_visible()
        expect(page.locator(".st-key-edit_notice")).to_contain_text("发送将替换原回合")
        page.get_by_role("button", name="取消编辑", exact=True).click()
        expect(page.get_by_role("button", name="取消编辑", exact=True)).to_have_count(0)
        expect(prompt).to_have_value("")
        ask("取消后的新问题")
        expect(page.locator('[data-testid="stChatMessage"]')).to_have_count(8)
        page.get_by_role("button", name="重新编辑问题", exact=True).click()
        nav("系统设置")
        nav("AI 聊天")
        expect(page.get_by_role("button", name="取消编辑", exact=True)).to_have_count(0)
        ask("切页后的新问题")
        page.get_by_role("button", name="重新编辑问题", exact=True).click()
        expect(prompt).to_have_value("故障第二轮")
        prompt.press("ControlOrMeta+A")
        prompt.press("Backspace")
        expect(page.get_by_role("button", name="取消编辑", exact=True)).to_be_visible()
        prompt.press("Escape")
        expect(page.get_by_role("button", name="取消编辑", exact=True)).to_have_count(0)
        expect(prompt).to_have_value("")
        ask("清空后的新问题")
        expect(page.locator('[data-testid="stChatMessage"]')).to_have_count(12)
        checks.append("edit_indicator_cancel_navigation_and_explicit_escape_start_new_turn")

        (workspace / ".allow-retry").write_text("test only")
        (workspace / ".fail-retry-save").write_text("test only")
        page.get_by_role("button", name="重试此问题", exact=True).click()
        status = page.locator(".generation-status")
        status.wait_for()
        messages = page.locator('[data-testid="stChatMessage"]')
        expect(messages).to_have_count(12)
        assert messages.nth(3).locator(".generation-status").count() == 1
        assert "第三轮" in messages.nth(4).inner_text()
        (workspace / ".release-retry").write_text("release")
        page.get_by_text("回答已生成，但未保存到历史记录。", exact=False).wait_for()
        expect(messages).to_have_count(12)
        assert "未保存" in messages.nth(3).inner_text()
        assert "第三轮" in messages.nth(4).inner_text()
        page.screenshot(path=str(root / "artifacts/review-round6-timeline.png"), full_page=True)
        page.get_by_role("button", name="移除此未保存记录", exact=True).click()
        expect(page.get_by_role("button", name="重试此问题", exact=True)).to_be_visible()
        (workspace / ".fail-retry-save").unlink()
        page.get_by_role("button", name="重新编辑问题", exact=True).click()
        expect(prompt).to_have_value("故障第二轮")
        prompt.fill("故障修订第二轮")
        prompt.press("Enter")
        page.get_by_text("已回答：故障修订第二轮", exact=True).wait_for()
        expect(messages).to_have_count(12)
        assert "故障修订第二轮" in messages.nth(2).inner_text()
        assert "第三轮" in messages.nth(4).inner_text()
        expect(page.get_by_role("button", name="取消编辑", exact=True)).to_have_count(0)
        checks.append(
            "middle_retry_stream_and_unsaved_overlay_keep_original_position_and_pair_count"
        )

        nav("知识库管理")
        page.locator('input[type="file"]').set_input_files(
            [
                {
                    "name": "记录.md",
                    "mimeType": "text/markdown",
                    "buffer": ("保管记录及相关边界。" * 100).encode(),
                },
                {
                    "name": "补充.md",
                    "mimeType": "text/markdown",
                    "buffer": ("保管记录需要核对。" * 100).encode(),
                },
            ]
        )
        page.get_by_role("button", name="导入并建立索引").click()
        page.get_by_text("2 个文件已处理", exact=False).wait_for()
        nav("AI 聊天")
        old = page.locator(".interaction-scope").get_attribute("data-render")
        page.get_by_role("button", name="新建会话", exact=True).click()
        expect(page.locator(".interaction-scope")).not_to_have_attribute("data-render", old)
        prompt.fill("保管围栏案例")
        prompt.press("Enter")
        code = page.locator('[class*="st-key-answer_body_"] pre code')
        expect(code).to_have_text("x=1\n")
        expect(page.get_by_text("最后的普通正文。", exact=False)).to_be_visible()
        link = page.get_by_role("link", name="说明", exact=True)
        expect(link).to_have_attribute("href", "https://example.com")
        page.get_by_role("button", name="复制回答", exact=True).click()
        expect(page.locator(".copy-answer")).to_have_attribute("data-state", "copied")
        text = page.evaluate("navigator.clipboard.readText()").replace("\r\n", "\n")
        assert "x=1\n```" in text and "〔来源" not in text
        page.screenshot(path=str(root / "artifacts/review-round6-markdown.png"), full_page=True)
        checks.append("unclosed_fence_backslash_reference_definition_and_plain_clipboard")

        nav("系统设置")
        size = page.get_by_role("spinbutton", name="Chunk Size（字符）")
        size.fill("500")
        page.get_by_role("button", name="保存设置", exact=True).click()
        feedback = page.locator(".st-key-settings_feedback")
        expect(feedback).to_contain_text("需要重建")
        page.get_by_role("spinbutton", name="Top-K").fill("8")
        page.get_by_role("button", name="保存设置", exact=True).click()
        expect(feedback).to_contain_text("需要重建")
        expect(page.get_by_role("button", name="立即重建索引", exact=True)).to_be_visible()
        page.get_by_role("button", name="恢复页面默认设置", exact=True).click()
        expect(feedback).to_contain_text("无需重建")
        expect(page.get_by_role("button", name="立即重建索引", exact=True)).to_have_count(0)
        size.fill("500")
        page.get_by_role("button", name="保存设置", exact=True).click()
        expect(feedback).to_contain_text("需要重建")
        nav("知识库管理")
        raw = next((workspace / "originals").glob("*.md"))
        backup = workspace / "qa-original.backup"
        raw.replace(backup)
        try:
            page.get_by_text("索引维护", exact=True).click()
            page.get_by_role("button", name="重新建立索引", exact=True).click()
            expect(page.get_by_role("heading", name="知识库管理", exact=True)).to_be_visible()
            expect(page.get_by_text("原始文件无法读取", exact=False)).to_be_visible()
            expect(page.get_by_role("button", name="重新建立索引", exact=True)).to_be_visible()
        finally:
            backup.replace(raw)
        page.get_by_role("button", name="重新建立索引", exact=True).click()
        page.get_by_text("索引重建完成，全部文档已就绪。", exact=True).wait_for()
        nav("系统设置")
        expect(feedback).to_contain_text("已就绪")
        checks.append("actual_index_status_survives_second_save_defaults_and_local_rebuild_errors")

        nav("AI 聊天")
        page.get_by_text("查看 2 条引用来源", exact=True).click()
        page.wait_for_timeout(1100)
        page.set_viewport_size({"width": 700, "height": 900})
        page.wait_for_function(
            "document.querySelector('[data-testid=stSidebarContent]').getBoundingClientRect().right<=0"
        )
        main = page.locator('[data-testid="stMain"]')
        main.evaluate("e=>e.scrollTop=e.scrollHeight")
        page.wait_for_timeout(300)
        before = page.locator(".st-key-composer").bounding_box()
        main.evaluate("e=>e.scrollTop=0")
        page.mouse.move(500, 300)
        page.mouse.wheel(0, -600)
        jump = page.get_by_role("button", name="回到最新消息", exact=True)
        expect(jump).to_be_visible()
        shown = page.locator(".st-key-composer").bounding_box()
        assert abs(shown["height"] - before["height"]) < 1
        jump.click()
        expect(jump).to_be_hidden()
        page.wait_for_timeout(700)
        after = page.locator(".st-key-composer").bounding_box()
        assert abs(after["y"] - shown["y"]) < 1 and abs(after["height"] - shown["height"]) < 1
        bottom = messages.last.bounding_box()
        assert abs(bottom["y"] + bottom["height"] - (after["y"] - 64)) < 3
        mask = page.locator(".conversation-header").evaluate(
            "e=>getComputedStyle(e,'::before').maskImage"
        )
        assert "linear-gradient" in mask
        page.screenshot(path=str(root / "artifacts/review-round6-narrow.png"), full_page=True)
        checks.append("narrow_jump_no_height_shift_exact_anchor_and_soft_header_edge")
        assert page.locator('[data-testid="stException"]').count() == 0
        browser.close()
    report = {
        "passed": True,
        "checks": checks,
        "viewports": [[1440, 900], [700, 900]],
        "paid_api_calls": 0,
    }
    (root / "artifacts/review-round6-browser.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
