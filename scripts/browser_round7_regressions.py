"""Real Edge checks using only the isolated round7 fake-provider workspace."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8534")
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
        assert (workspace / ".review-fixture").read_text() == "round7"

        def nav(name):
            page.get_by_role("radio", name=name, exact=True).check(force=True)
            expect(page.locator(".interaction-scope")).to_have_attribute("data-page", name)

        def ask(text, failure=False):
            prompt.fill(text)
            prompt.press("Enter")
            if failure:
                page.get_by_role("button", name="重新编辑问题", exact=True).wait_for()
            else:
                page.get_by_text("已回答：" + text, exact=True).wait_for()

        nav("系统设置")
        page.get_by_role("button", name="保存设置", exact=True).click()
        feedback = page.locator(".st-key-settings_feedback")
        expect(feedback).to_contain_text("为空知识库")
        nav("AI 聊天")
        ask("第一轮")
        ask("故障第二轮", True)
        for i in range(3, 9):
            ask(f"第{i}轮正常问题")
        messages = page.locator('[data-testid="stChatMessage"]')
        expect(messages).to_have_count(16)
        edit = page.get_by_role("button", name="重新编辑问题", exact=True)
        cancel = page.get_by_role("button", name="取消编辑", exact=True)
        edit.click()
        expect(prompt).to_have_value("故障第二轮")
        prompt.press("ControlOrMeta+A")
        prompt.press("Backspace")
        page.keyboard.insert_text("重写时不应被清空")
        page.wait_for_timeout(900)
        expect(cancel).to_be_visible()
        expect(prompt).to_have_value("重写时不应被清空")
        prompt.press("Escape")
        expect(cancel).to_have_count(0)
        expect(prompt).to_have_value("")
        edit.click()
        expect(prompt).to_have_value("故障第二轮")
        prompt.press("ControlOrMeta+A")
        prompt.press("Delete")
        page.keyboard.insert_text("故障修订第二轮")
        page.wait_for_timeout(700)
        expect(cancel).to_be_visible()
        expect(prompt).to_have_value("故障修订第二轮")
        page.screenshot(path=str(root / "artifacts/review-round7-edit.png"), full_page=True)
        checks.append("empty_then_immediate_retyping_preserves_edit_and_characters_escape_cancels")

        (workspace / ".allow-retry").write_text("test only")
        page.evaluate("""() => {
          window.retryAnimations=[];
          document.addEventListener('animationstart',e=>{
            if(e.target.matches('[data-testid=stChatMessage]')) window.retryAnimations.push(
              [...document.querySelectorAll('[data-testid=stChatMessage]')].indexOf(e.target));
          });
        }""")
        prompt.press("Enter")
        status = page.locator(".generation-status")
        status.wait_for()
        page.wait_for_timeout(900)
        expect(messages).to_have_count(16)
        assert messages.nth(3).locator(".generation-status").count() == 1
        position = status.bounding_box()
        composer = page.locator(".st-key-composer").bounding_box()
        assert position["y"] >= 60 and position["y"] + position["height"] < composer["y"]
        assert messages.last.bounding_box()["y"] > 900
        assert 3 in page.evaluate("window.retryAnimations")
        page.screenshot(path=str(root / "artifacts/review-round7-retry.png"), full_page=True)
        page.mouse.move(700, 300)
        page.mouse.wheel(0, -250)
        jump = page.get_by_role("button", name="回到最新消息", exact=True)
        expect(jump).to_be_visible()
        jump.click()
        page.wait_for_timeout(1000)
        main = page.locator('[data-testid="stMain"]')
        tail_top = main.evaluate("e=>e.scrollTop")
        page.wait_for_timeout(700)
        assert abs(main.evaluate("e=>e.scrollTop") - tail_top) < 3
        (workspace / ".release-retry").write_text("release")
        page.get_by_text("已回答：故障修订第二轮", exact=True).wait_for()
        page.wait_for_timeout(700)
        expect(messages).to_have_count(16)
        tail = messages.last.bounding_box()
        composer = page.locator(".st-key-composer").bounding_box()
        assert abs(tail["y"] + tail["height"] - (composer["y"] - 20)) < 3
        assert "故障修订第二轮" in messages.nth(2).inner_text()
        checks.append(
            "middle_retry_follows_active_turn_animates_and_explicit_tail_choice_is_respected"
        )

        (workspace / ".allow-retry").unlink()
        ask("故障第九轮", True)
        ask("第十轮正常问题")
        for i in range(11, 15):
            ask(f"第{i}轮正常问题")
        (workspace / ".allow-retry").write_text("test only")
        (workspace / ".release-retry").unlink()
        edit.click()
        expect(prompt).to_have_value("故障第九轮")
        prompt.fill("故障重写第九轮")
        prompt.press("Enter")
        status.wait_for()
        page.wait_for_timeout(800)
        assert status.bounding_box()["y"] >= 60
        (workspace / ".release-retry").write_text("release")
        page.get_by_text("已回答：故障重写第九轮", exact=True).wait_for()
        page.wait_for_timeout(900)
        current = messages.nth(17).bounding_box()
        composer = page.locator(".st-key-composer").bounding_box()
        assert current["y"] >= 60 and current["y"] + current["height"] < composer["y"]
        assert messages.last.bounding_box()["y"] > composer["y"]
        checks.append("middle_retry_completion_keeps_same_visible_anchor_without_tail_jump")

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
        nav("系统设置")
        expect(feedback).to_contain_text("已就绪")
        expect(feedback).not_to_contain_text("为空知识库")
        index = max(
            (workspace / "indexes").glob("*/index.faiss"), key=lambda p: p.stat().st_mtime_ns
        )
        backup = workspace / "qa-index.backup"
        index.replace(backup)
        try:
            nav("AI 聊天")
            nav("系统设置")
            expect(feedback).to_contain_text("需要重建")
            expect(page.get_by_role("button", name="立即重建索引", exact=True)).to_be_visible()
            fresh = context.new_page()
            fresh.goto(args.url)
            fresh.get_by_role("radio", name="系统设置", exact=True).check(force=True)
            expect(fresh.locator(".st-key-settings_feedback")).to_contain_text("需要重建")
            expect(fresh.get_by_role("button", name="立即重建索引", exact=True)).to_be_visible()
            fresh.close()
        finally:
            backup.replace(index)
        checks.append("settings_feedback_updates_empty_ready_corrupt_and_without_prior_save")

        nav("AI 聊天")
        old = page.locator(".interaction-scope").get_attribute("data-render")
        page.get_by_role("button", name="新建会话", exact=True).click()
        expect(page.locator(".interaction-scope")).not_to_have_attribute("data-render", old)
        prompt.fill("保管围栏案例")
        prompt.press("Enter")
        code = page.locator('[class*="st-key-answer_body_"] pre code')
        expect(code).to_have_count(2)
        expect(code.nth(0)).to_have_text("x=1\n")
        expect(code.nth(1)).to_have_text("y=2\n")
        expect(page.get_by_role("link", name="说明", exact=True)).to_have_attribute(
            "href", "https://example.com"
        )
        page.get_by_role("button", name="复制回答", exact=True).click()
        expect(page.locator(".copy-answer")).to_have_attribute("data-state", "copied")
        text = page.evaluate("navigator.clipboard.readText()").replace("\r\n", "\n")
        assert "引用块外的普通段落。\n> ```" not in text and "〔来源" not in text
        assert "> y=2\n\n引用块外的普通段落。" in text
        page.screenshot(path=str(root / "artifacts/review-round7-markdown.png"), full_page=True)
        checks.append("container_fence_has_no_ghost_code_and_plain_clipboard_has_no_extra_fence")
        page.get_by_text("查看 2 条引用来源", exact=True).click()
        page.wait_for_timeout(1000)
        page.set_viewport_size({"width": 700, "height": 900})
        page.wait_for_timeout(500)
        main.evaluate("e=>e.scrollTop=0")
        page.mouse.move(500, 300)
        page.mouse.wheel(0, -500)
        expect(jump).to_be_visible()
        height = page.locator(".st-key-composer").bounding_box()["height"]
        jump.click()
        expect(jump).to_be_hidden()
        page.wait_for_timeout(700)
        assert abs(page.locator(".st-key-composer").bounding_box()["height"] - height) < 1
        page.screenshot(path=str(root / "artifacts/review-round7-narrow.png"), full_page=True)
        checks.append("narrow_view_keeps_stable_composer_and_expanded_sources")
        assert page.locator('[data-testid="stException"]').count() == 0
        browser.close()
    report = {
        "passed": True,
        "checks": checks,
        "viewports": [[1440, 900], [700, 900]],
        "paid_api_calls": 0,
    }
    (root / "artifacts/review-round7-browser.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
