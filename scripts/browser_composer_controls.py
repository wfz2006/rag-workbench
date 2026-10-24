"""Counter/send/stop geometry. Requires the explicit local SSE fixture and isolated QA data."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8518")
args = parser.parse_args()
output = Path(__file__).resolve().parents[1] / "artifacts"
results, failures = [], []

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    for width, height, scale in ((1280, 720, 1), (1048, 696, 1.5), (390, 760, 1)):
        context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=scale,
            color_scheme="dark",
        )
        page = context.new_page()
        page.goto(args.url)
        new_chat = page.get_by_role("button", name="新建会话", exact=True)
        new_chat.wait_for(state="attached")
        if new_chat.bounding_box()["x"] < 0:
            page.get_by_role("button", name="Expand sidebar").click()
        new_chat.click()
        page.get_by_text("今天，想弄清楚什么？", exact=True).wait_for()
        if width < 768:
            page.get_by_role("button", name="Collapse sidebar").click()
        question = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
        checks = {}
        checks["empty_placeholder_has_full_width"] = question.evaluate(
            "e=>parseFloat(getComputedStyle(e).paddingRight)<60"
        )
        for name, text in (
            ("short", "测试计数器"),
            ("limit", "字" * 1999),
            ("multiline", "多行输入\n" * 15),
        ):
            question.fill(text)
            counter = page.locator("#stChatInputInstructions")
            expect(counter).to_contain_text(f"{len(text)}/2000")
            counter_bounds = counter.bounding_box()
            send = page.get_by_role("button", name="Send message")
            send_bounds = send.bounding_box()
            checks[f"{name}_counter_clear_of_send"] = (
                counter_bounds["x"] + counter_bounds["width"] + 6 <= send_bounds["x"]
            )
            checks[f"{name}_text_gutter"] = question.evaluate(
                """e=>{
                    const r=e.getBoundingClientRect();
                    const p=parseFloat(getComputedStyle(e).paddingRight);
                    const c=document.querySelector('#stChatInputInstructions');
                    return r.right-p<=c.getBoundingClientRect().left-4;
                }"""
            )
            page.locator(".st-key-prompt_surface").screenshot(
                path=str(output / f"composer-counter-{width}-{scale}-{name}.png")
            )
        question.fill("stop")
        question.press("Enter")
        stop = page.get_by_role("button", name="停止生成", exact=True)
        stop.wait_for()
        checks["stop_solid_square"] = stop.evaluate(
            """e=>{
                const s=getComputedStyle(e,'::after');
                return s.width==='10px'&&s.height==='10px'&&s.backgroundColor!=='rgba(0, 0, 0, 0)';
            }"""
        )
        stop_bounds = stop.bounding_box()
        surface = page.locator(".st-key-prompt_surface").bounding_box()
        checks["stop_matches_send_slot"] = (
            abs(stop_bounds["width"] - 32) < 1
            and abs(stop_bounds["height"] - 32) < 1
            and abs(
                surface["x"] + surface["width"] - stop_bounds["x"] - stop_bounds["width"] / 2 - 24
            )
            < 2
        )
        page.locator(".st-key-prompt_surface").screenshot(
            path=str(output / f"composer-stop-{width}-{scale}.png")
        )
        stop.click()
        page.get_by_text("已停止生成。", exact=False).wait_for()
        expect(question).to_be_enabled()
        checks["cancel_and_resume_input"] = True
        assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
        assert page.locator('[data-testid="stMain"]').evaluate("e=>e.scrollWidth<=e.clientWidth")
        results.append({"width": width, "scale": scale, "checks": checks})
        failures.extend(f"{width}/{scale}: {key}" for key, passed in checks.items() if not passed)
        context.close()
    browser.close()

print(json.dumps(results, indent=2))
assert not failures, failures
