"""Screenshot-led acceptance against round4_app.py, in a disposable workspace."""

import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8529")
    parser.add_argument("--workspace", type=Path, default=Path("artifacts/review-round4-ui"))
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
        page.set_default_timeout(10000)
        page.goto(args.url)

        def new_chat():
            scope = page.locator(".interaction-scope")
            previous = scope.get_attribute("data-render")
            page.get_by_role("button", name="新建会话", exact=True).click()
            expect(scope).not_to_have_attribute("data-render", previous)
            expect(page.locator(".conversation-header")).to_contain_text("新会话")

        prompt = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
        prompt.wait_for()
        assert (workspace / ".review-fixture").read_text() == "round4"
        for name in (".api-enabled", ".release-save", ".release-generate"):
            (workspace / name).unlink(missing_ok=True)
        new_chat()
        prompt.fill("重试测试问题")
        prompt.press("Enter")
        page.get_by_role("button", name="重试此问题").wait_for()
        assert "重启" not in page.locator('[data-testid="stAlert"]').inner_text()
        assert page.get_by_text("检索与路由详情", exact=True).count() == 0
        page.get_by_role("button", name="重新编辑问题").click()
        expect(prompt).to_have_value("重试测试问题")
        prompt.fill("")
        (workspace / ".api-enabled").write_text("enabled mock")
        page.get_by_role("button", name="重试此问题").click()
        page.locator('[data-copy-target^="answer_body_"]').wait_for()
        # Retries now replace the failed pair in place, rather than adding a duplicate turn.
        expect(page.get_by_role("button", name="重试此问题")).to_have_count(0)
        page.get_by_role("button", name="复制回答").click()
        assert "通用模式" not in page.evaluate("navigator.clipboard.readText()")
        checks.append("error_refill_retry_remove_and_plain_general_clipboard")

        page.get_by_role("radio", name="知识库管理", exact=True).check(force=True)
        page.locator('input[type="file"]').set_input_files(
            [
                {
                    "name": "先上传_记录.md",
                    "mimeType": "text/markdown",
                    "buffer": ("记录保管三年。到期后应进行复核。" * 40).encode(),
                },
                {
                    "name": "后上传_栈说明.txt",
                    "mimeType": "text/plain",
                    "buffer": "栈支持入栈和出栈。".encode(),
                },
            ]
        )
        page.get_by_role("button", name="导入并建立索引").click()
        page.get_by_text("2 个文件已处理", exact=False).wait_for()
        select = page.get_by_role("combobox", name="选择要移除的文档")
        expect(select).to_have_value(re.compile("先上传_记录.md"))
        assert "已就绪" in page.locator('[data-testid="stMetricValue"]').all_text_contents()
        page.screenshot(path=str(root / "artifacts/review-round4-kb.png"), full_page=True)
        checks.append("document_sort_localized_status_and_short_identifier_table")

        page.get_by_role("radio", name="AI 聊天", exact=True).check(force=True)
        new_chat()
        prompt.fill("保管期限和到期处理")
        prompt.press("Enter")
        sources = page.get_by_text("查看 1 条引用来源", exact=True)
        sources.wait_for()
        page.get_by_role("button", name="复制回答", exact=True).click()
        copied = page.evaluate("navigator.clipboard.readText()")
        assert "〔来源" not in copied and "保管期限" in copied
        assert (
            page.locator(
                '[class*="st-key-answer_body_"] [data-testid="stMarkdownContainer"] p'
            ).count()
            == 2
        )
        sources.click()
        page.wait_for_timeout(650)  # The native expander animates for roughly half a second.
        panel = page.locator("details").filter(
            has=page.get_by_text("查看 1 条引用来源", exact=True)
        )
        page.wait_for_function("""() => {
            const panel=[...document.querySelectorAll('details')].find(e=>
                e.querySelector('summary')?.textContent.includes('查看 1 条引用来源'));
            const r=panel?.getBoundingClientRect();
            const c=document.querySelector('.st-key-composer')?.getBoundingClientRect();
            return r && c && (r.height>c.top-88 ? r.top>=60 && r.top<100 : r.bottom<=c.top-12);
        }""")
        page.locator('[data-testid="stMain"]').evaluate("e=>e.scrollTop=e.scrollHeight")
        page.wait_for_timeout(250)
        page.screenshot(
            path=str(root / "artifacts/review-round4-scroll-inspect.png"), full_page=True
        )
        assert (
            panel.bounding_box()["y"] + panel.bounding_box()["height"]
            <= page.locator(".st-key-composer").bounding_box()["y"] - 10
        ), page.locator('[data-testid="stMain"]').evaluate(
            """e=>({top:e.scrollTop,height:e.clientHeight,full:e.scrollHeight,
            panel:[...document.querySelectorAll('details')].find(d=>d.open)?.getBoundingClientRect().toJSON(),
            composer:document.querySelector('.st-key-composer').getBoundingClientRect().toJSON(),
            padding:getComputedStyle(document.querySelector('[data-testid=stMainBlockContainer]')).paddingBottom})"""
        )
        assert "无固定页码" in panel.inner_text() and "字符" in panel.inner_text()
        mask = page.locator(".conversation-header").evaluate(
            "e=>getComputedStyle(e,'::before').backgroundColor"
        )
        assert mask not in ("transparent", "rgba(0, 0, 0, 0)")
        page.screenshot(path=str(root / "artifacts/review-round4-sources.png"), full_page=True)
        checks.append("compact_citations_plain_copy_auto_reveal_scroll_padding_and_header_mask")

        page.get_by_role("radio", name="系统设置", exact=True).check(force=True)
        page.get_by_text("聊天中显示检索调试信息", exact=True).click()
        page.get_by_role("button", name="保存设置", exact=True).click()
        feedback = page.locator(".st-key-settings_feedback")
        expect(feedback).to_contain_text("无需重建索引")
        rect = feedback.bounding_box()
        assert rect["y"] < 900 and rect["y"] + rect["height"] > 0
        page.get_by_role("spinbutton", name="Chunk Size（字符）").fill("420")
        page.get_by_role("button", name="保存设置", exact=True).click()
        expect(feedback).to_contain_text("需要重建")
        page.get_by_role("button", name="立即重建索引", exact=True).click()
        expect(feedback).to_contain_text("索引重建完成")
        page.get_by_role("button", name="恢复页面默认设置").click()
        expect(page.get_by_role("spinbutton", name="Chunk Size（字符）")).to_have_value("400")
        page.get_by_role("button", name="立即重建索引", exact=True).click()
        page.screenshot(path=str(root / "artifacts/review-round4-settings.png"), full_page=True)
        checks.append("in_view_save_feedback_immediate_vs_reindex_and_restore_defaults")

        page.get_by_role("radio", name="AI 聊天", exact=True).check(force=True)
        search = page.get_by_role("button", name="搜索会话", exact=True)
        search.click()
        page.get_by_role("textbox", name="搜索会话", exact=True).fill("绝对没有这个标题")
        page.get_by_role("textbox", name="搜索会话", exact=True).press("Enter")
        page.keyboard.press("Escape")
        expect(page.get_by_role("button", name="清除筛选", exact=True)).to_be_visible()
        page.get_by_role("button", name="清除筛选", exact=True).click()
        expect(page.get_by_role("button", name="清除筛选", exact=True)).to_have_count(0)
        search.click()
        page.set_viewport_size({"width": 700, "height": 900})
        expect(page.locator('[data-testid="stPopoverBody"]')).to_have_count(0)
        expect(page.locator('[data-testid="stSidebar"]')).to_have_attribute(
            "data-collapsed", "true"
        )
        page.wait_for_function(
            "document.querySelector('[data-testid=stSidebarContent]').getBoundingClientRect().right<=0"
        )
        page.mouse.move(690, 600)
        page.screenshot(path=str(root / "artifacts/review-round4-narrow.png"), full_page=True)
        checks.append("visible_search_filter_clear_and_resize_popover_dismiss")

        page.set_viewport_size({"width": 1440, "height": 900})
        if not page.get_by_role("button", name="新建会话", exact=True).is_visible():
            page.get_by_role("button", name="Expand sidebar").click()
        new_chat()
        prompt.fill("等待生成中")
        prompt.press("Enter")
        page.get_by_role("button", name="停止生成", exact=True).wait_for()
        running_id = page.locator(".interaction-scope").get_attribute("data-session")
        new_chat()
        running = page.locator(f".st-key-session_{running_id} button:visible")
        expect(running).to_contain_text("等待生成中")
        running.click()
        page.get_by_role("button", name="停止生成", exact=True).click()
        page.get_by_text("已停止", exact=True).wait_for()
        checks.append("running_empty_session_stays_reachable_and_cancellable")

        new_chat()
        prompt.fill("保管慢保存")
        prompt.press("Enter")
        page.get_by_text("正在保存记录", exact=True).wait_for()
        new_chat()
        expect(prompt).to_be_enabled()
        (workspace / ".release-save").write_text("release")
        checks.append("slow_persistence_does_not_freeze_navigation")

        page.get_by_role("radio", name="系统设置", exact=True).check(force=True)
        page.get_by_text("聊天中显示检索调试信息", exact=True).click()
        page.get_by_role("button", name="保存设置", exact=True).click()
        expect(page.locator(".st-key-settings_feedback")).to_contain_text("无需重建索引")
        page.get_by_role("radio", name="AI 聊天", exact=True).check(force=True)
        new_chat()
        prompt.fill("保管未保存")
        prompt.press("Enter")
        page.get_by_text("回答已生成，但未保存到历史记录。", exact=False).wait_for()
        page.get_by_text("检索与路由详情", exact=True).click()
        page.get_by_text("支持证据 ID（1 条）", exact=True).wait_for()
        candidate = page.locator("summary").filter(has_text="已选为支持证据").first
        candidate.click()
        expect(page.get_by_text("关键词得分（BM25）：", exact=False).first).to_be_visible()
        page.locator('[data-copy-target^="answer_body_unsaved_"]').click()
        assert "记录的保管期限为三年。" in page.evaluate("navigator.clipboard.readText()")
        checks.append("unsaved_answer_keeps_debug_support_marker_lexical_scores_and_plain_copy")
        page.emulate_media(color_scheme="dark")
        page.wait_for_function(
            "getComputedStyle(document.querySelector('[data-testid=stApp]')).colorScheme==='dark'"
        )
        page.mouse.move(1420, 600)
        page.wait_for_timeout(500)  # Theme transitions must finish before visual acceptance.
        page.screenshot(path=str(root / "artifacts/review-round4-dark.png"), full_page=True)
        checks.append("dark_theme_rendering")
        assert page.locator('[data-testid="stException"]').count() == 0
        browser.close()
    report = {
        "passed": True,
        "checks": checks,
        "viewports": [[1440, 900], [700, 900]],
        "paid_api_calls": 0,
    }
    (root / "artifacts/review-round4-browser.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
