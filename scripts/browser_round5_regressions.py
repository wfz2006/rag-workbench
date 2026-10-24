"""Actual Edge acceptance with the explicitly isolated round5_app.py fixture."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8531")
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    workspace = args.workspace.resolve()
    assert workspace.is_relative_to(root / "artifacts")
    checks = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()
        page.set_default_timeout(12000)
        page.goto(args.url)
        prompt = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
        prompt.wait_for()
        assert (workspace / ".review-fixture").read_text() == "round5"

        def navigate(name):
            page.get_by_role("radio", name=name, exact=True).check(force=True)
            expect(page.locator(".interaction-scope")).to_have_attribute("data-page", name)

        def new_chat():
            scope = page.locator(".interaction-scope")
            old = scope.get_attribute("data-render")
            page.get_by_role("button", name="新建会话", exact=True).click()
            expect(scope).not_to_have_attribute("data-render", old)
            expect(page.locator(".conversation-header")).to_contain_text("新会话")

        prompt.fill("旧的错误标题")
        prompt.press("Enter")
        page.get_by_role("button", name="移除此失败回合").click()
        expect(page.locator(".conversation-header")).to_contain_text("新会话")
        prompt.fill("现在正确的问题")
        prompt.press("Enter")
        page.get_by_role("button", name="重试此问题").wait_for()
        (workspace / ".api-enabled").write_text("test only")
        page.get_by_role("button", name="重试此问题").click()
        page.get_by_role("button", name="复制回答").wait_for()
        expect(page.locator('[data-testid="stChatMessage"]')).to_have_count(2)
        expect(page.get_by_role("button", name="重试此问题")).to_have_count(0)
        expect(page.locator(".conversation-header")).to_contain_text("现在正确的问题")
        checks.append("remove_failure_resets_title_and_retry_replaces_in_place")

        (workspace / ".api-enabled").unlink()
        prompt.fill("待修改的错误问题")
        prompt.press("Enter")
        page.get_by_role("button", name="重新编辑问题").click()
        expect(prompt).to_have_value("待修改的错误问题")
        prompt.fill("重新编辑后的问题")
        (workspace / ".api-enabled").write_text("test only")
        prompt.press("Enter")
        expect(page.get_by_role("button", name="重试此问题")).to_have_count(0)
        expect(page.locator('[data-testid="stChatMessage"]')).to_have_count(4)
        checks.append("edited_failure_is_replaced_without_duplicate_history")

        navigate("知识库管理")
        page.locator('input[type="file"]').set_input_files(
            [
                {
                    "name": "记录.md",
                    "mimeType": "text/markdown",
                    "buffer": ("记录保管三年，到期复核。" * 120).encode(),
                },
                {
                    "name": "代码.md",
                    "mimeType": "text/markdown",
                    "buffer": ("代码可以使用 vector<int>，需要检查边界。" * 120).encode(),
                },
            ]
        )
        page.get_by_role("button", name="导入并建立索引").click()
        page.get_by_text("2 个文件已处理", exact=False).wait_for()
        page.locator('[data-testid="stDataFrame"]').scroll_into_view_if_needed()
        page.screenshot(path=str(root / "artifacts/review-round5-table.png"), full_page=True)
        navigate("AI 聊天")
        new_chat()
        prompt.fill("保管代码案例")
        prompt.press("Enter")
        copy = page.get_by_role("button", name="复制回答", exact=True)
        copy.wait_for()
        code = page.locator('[class*="st-key-answer_body_"] pre code')
        expect(code).to_have_count(1)
        expect(code).to_have_text("vector<int> xs;\n")
        expect(page.locator('[class*="st-key-answer_body_"] table tbody td').last).to_have_text("b")
        expect(
            page.locator('[class*="st-key-answer_body_"] p').filter(has_text="独立正文")
        ).to_have_count(1)
        body = page.locator('[class*="st-key-answer_body_"]').inner_text()
        assert body.index("〔来源 1〕") < body.index("〔来源 2〕")
        copy.click()
        expect(copy).to_have_attribute("data-state", "copied")
        copied = page.evaluate("navigator.clipboard.readText()").replace("\r\n", "\n")
        assert "〔来源" not in copied and "```cpp\nvector<int> xs;\n```" in copied, repr(copied)
        page.screenshot(path=str(root / "artifacts/review-round5-markdown.png"), full_page=True)
        checks.append("fenced_code_and_table_intact_first_citation_order_plain_copy")

        navigate("系统设置")
        page.get_by_text("聊天中显示检索调试信息", exact=True).click()
        page.get_by_role("button", name="保存设置", exact=True).click()
        feedback = page.locator(".st-key-settings_feedback")
        expect(feedback).to_contain_text("设置已保存")
        size = page.get_by_role("spinbutton", name="Chunk Size（字符）")
        overlap = page.get_by_role("spinbutton", name="Chunk Overlap（字符）")
        size.fill("100")
        overlap.fill("200")
        page.get_by_role("button", name="保存设置", exact=True).click()
        expect(feedback).to_contain_text("设置无效")
        expect(feedback.locator('[data-testid="stAlertContentSuccess"]')).to_have_count(0)
        page.screenshot(
            path=str(root / "artifacts/review-round5-invalid-settings.png"), full_page=True
        )
        checks.append("invalid_save_replaces_old_success_feedback")
        before = len((workspace / "resource-builds.txt").read_text().splitlines())
        size.fill("420")
        overlap.fill("60")
        page.get_by_role("button", name="保存并立即重建索引", exact=True).click()
        expect(feedback).to_contain_text("索引重建完成", timeout=20000)
        after = len((workspace / "resource-builds.txt").read_text().splitlines())
        assert after == before + 1, (before, after)
        size.fill("440")
        page.get_by_role("button", name="保存设置", exact=True).click()
        expect(feedback).to_contain_text("需要重建")
        navigate("知识库管理")
        page.get_by_text("索引维护", exact=True).click()
        page.get_by_role("button", name="重新建立索引", exact=True).click()
        page.get_by_text("索引重建完成", exact=False).wait_for()
        navigate("系统设置")
        expect(feedback).to_contain_text("当前索引已就绪")
        expect(page.get_by_role("button", name="立即重建索引", exact=True)).to_have_count(0)
        checks.append("single_cached_resource_build_and_cross_page_reindex_feedback_refresh")

        navigate("AI 聊天")
        page.get_by_text("查看 2 条引用来源", exact=True).click()
        page.wait_for_timeout(1000)
        page.set_viewport_size({"width": 700, "height": 900})
        page.wait_for_function(
            "document.querySelector('[data-testid=stSidebarContent]').getBoundingClientRect().right<=0"
        )
        page.locator('[data-testid="stMain"]').evaluate("e=>e.scrollTop=0")
        page.mouse.move(500, 350)
        page.mouse.wheel(0, -600)
        jump = page.get_by_role("button", name="回到最新消息", exact=True)
        expect(jump).to_be_visible()
        assert jump.evaluate("e=>!!e.closest('.st-key-composer')")
        j, c = jump.bounding_box(), page.locator(".st-key-composer").bounding_box()
        assert c["y"] <= j["y"] and j["y"] + j["height"] <= c["y"] + c["height"]
        page.screenshot(path=str(root / "artifacts/review-round5-narrow.png"), full_page=True)
        jump.click()
        expect(jump).to_be_hidden()
        checks.append("narrow_jump_docked_inside_composer_without_covering_transcript")
        assert page.locator('[data-testid="stException"]').count() == 0
        browser.close()
    report = {
        "passed": True,
        "checks": checks,
        "viewports": [[1440, 900], [700, 900]],
        "paid_api_calls": 0,
    }
    (root / "artifacts/review-round5-browser.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
