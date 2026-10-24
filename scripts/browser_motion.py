"""Real Edge interaction acceptance against the explicit tests/browser_support SSE fixture."""

import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8519")
args = parser.parse_args()
output = Path(__file__).resolve().parents[1] / "artifacts"
results = []

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    context = browser.new_context(
        viewport={"width": 1440, "height": 900}, permissions=["clipboard-read", "clipboard-write"]
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(
        "window.motionEvents=[];document.addEventListener('animationstart',e=>"
        "window.motionEvents.push(e.animationName));"
    )
    try:
        page.goto(args.url)
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name="新建会话", exact=True).click()
        page.get_by_text("今天，想弄清楚什么？", exact=True).wait_for()
        page.wait_for_function("!!window.__ragWorkbenchInteraction")
        question = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
        question.fill("输入法候选")
        question.dispatch_event(
            "keydown", {"key": "Enter", "code": "Enter", "keyCode": 229, "isComposing": True}
        )
        assert question.input_value() == "输入法候选"
        question.press("Shift+Enter")
        assert "\n" in question.input_value()
        question.fill("")
        results.append({"ime_and_shift_enter": "passed"})

        page.get_by_role("button", name="添加资料", exact=True).click()
        menu = page.locator('[data-testid="stPopoverBody"]')
        menu.wait_for()
        assert menu.evaluate("el=>getComputedStyle(el).animationName") == "wb-popover-in"
        page.screenshot(path=str(output / "motion-add-menu.png"))
        page.keyboard.press("Escape")
        menu.wait_for(state="hidden")
        page.get_by_role("button", name="添加资料", exact=True).click()
        menu.wait_for()
        page.mouse.click(1200, 200)
        menu.wait_for(state="hidden")
        page.get_by_role("button", name="deepseek-flash", exact=True).click()
        page.get_by_role("button", name="模型与连接设置", exact=True).click()
        page.get_by_role("button", name="保存设置", exact=True).wait_for()
        page.get_by_role("radio", name="AI 聊天", exact=True).check(force=True)
        question.wait_for()
        results.append({"menus_keyboard_outside_and_settings": "passed"})

        question.fill("long")
        question.press("Enter")
        stop = page.get_by_role("button", name="停止生成", exact=True)
        stop.wait_for()
        stream = page.locator(".st-key-stream_body")
        stream.get_by_text(re.compile("第 15 段")).wait_for()
        main = page.locator('[data-testid="stMain"]')
        assert main.evaluate("el=>el.scrollTop") > 0
        page.mouse.move(1000, 300)
        page.mouse.wheel(0, -650)
        jump = page.get_by_role("button", name="回到最新消息", exact=True)
        jump.wait_for()
        frozen = main.evaluate("el=>el.scrollTop")
        stream.get_by_text(re.compile("第 50 段")).wait_for()
        assert main.evaluate("el=>el.scrollTop") <= frozen + 12
        page.screenshot(path=str(output / "motion-scroll-paused.png"))
        jump.click()
        stop.wait_for(state="hidden")
        page.get_by_role("button", name="复制回答", exact=True).wait_for()
        assert "第 90 段" in page.locator('[class*="st-key-answer_body_"]').inner_text()
        page.locator('[data-testid="stChatMessage"]').last.hover()
        page.get_by_role("button", name="复制回答", exact=True).click()
        assert "第 90 段" in page.evaluate("navigator.clipboard.readText()")
        assert "wb-message-in" in page.evaluate("window.motionEvents")
        page.screenshot(path=str(output / "motion-stream-complete.png"))
        results.append({"stream_follow_pause_jump_copy_and_arrival": "passed"})

        page.get_by_role("button", name="新建会话", exact=True).click()
        page.get_by_text("今天，想弄清楚什么？", exact=True).wait_for()
        question.fill("stop")
        question.press("Enter")
        stream.get_by_text(re.compile("第 3 段")).wait_for()
        stop.click()
        page.get_by_text("已停止生成。", exact=False).wait_for()
        stop.wait_for(state="hidden")
        assert not stream.count()
        page.screenshot(path=str(output / "motion-cancelled.png"))
        results.append({"cancel_drops_incomplete_output": "passed"})

        for prompt, error in (("disconnect", "模型连接提前结束"), ("limit", "LLM 请求受限")):
            page.get_by_role("button", name="新建会话", exact=True).click()
            page.get_by_text("今天，想弄清楚什么？", exact=True).wait_for()
            question.fill(prompt)
            question.press("Enter")
            page.get_by_text(error, exact=False).wait_for()
            assert not stream.count()
        results.append({"disconnect_and_rate_limit": "passed"})
        assert not errors, errors
    except Exception:
        page.screenshot(path=str(output / "motion-failure.png"))
        (output / "motion-failure.html").write_text(page.content(), encoding="utf-8")
        raise
    finally:
        context.close()

    reduced = browser.new_context(viewport={"width": 390, "height": 760}, reduced_motion="reduce")
    page = reduced.new_page()
    page.goto(args.url)
    page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…").wait_for()
    question = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
    question.fill("short")
    question.press("Enter")
    page.locator(".generation-mark").wait_for()
    assert (
        page.locator(".generation-mark").evaluate("el=>getComputedStyle(el).animationName")
        == "none"
    )
    page.get_by_text("输出完成。", exact=False).wait_for()
    page.get_by_role("button", name="停止生成", exact=True).wait_for(state="hidden")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    page.screenshot(path=str(output / "motion-reduced-390.png"))
    results.append({"reduced_motion_and_mobile_stream": "passed"})
    reduced.close()

    dark = browser.new_context(viewport={"width": 1280, "height": 720}, color_scheme="dark")
    page = dark.new_page()
    page.goto(args.url)
    page.get_by_role("button", name="新建会话", exact=True).click()
    page.get_by_text("今天，想弄清楚什么？", exact=True).wait_for()
    page.get_by_role("button", name="Main menu").click()
    page.get_by_role("menuitemradio", name="Dark", exact=True).click()
    page.get_by_role("button", name="Main menu").click()
    page.get_by_role("button", name="添加资料", exact=True).click()
    menu = page.locator('[data-testid="stPopoverBody"]')
    menu.wait_for()
    assert menu.evaluate("el=>getComputedStyle(el).backgroundColor") == "rgb(48, 48, 47)"
    page.screenshot(path=str(output / "motion-menu-dark.png"))
    page.keyboard.press("Escape")
    menu.wait_for(state="hidden")
    question = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
    question.fill("short")
    question.press("Enter")
    page.locator(".generation-mark").wait_for()
    assert (
        page.locator(".generation-mark").evaluate("el=>getComputedStyle(el).animationName")
        == "wb-working"
    )
    page.get_by_role("button", name="停止生成", exact=True).wait_for(state="hidden")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    page.screenshot(path=str(output / "motion-dark-complete.png"))
    results.append({"dark_menu_and_stream": "passed"})
    dark.close()
    browser.close()

print(json.dumps(results, indent=2))
