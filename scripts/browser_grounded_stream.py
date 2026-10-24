"""KB browser acceptance against the explicit local SSE fixture and an isolated real BGE index."""

import argparse
import json
from pathlib import Path
from time import monotonic

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8519")
args = parser.parse_args()
output = Path(__file__).resolve().parents[1] / "artifacts"
results = []

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(args.url)
    page.get_by_role("button", name="新建会话", exact=True).click()
    page.get_by_text("今天，想弄清楚什么？", exact=True).wait_for()
    question = page.get_by_role("textbox", name="询问资料内容，或开始一个新话题…")
    question.fill("资料保管期限是多久？")
    question.press("Enter")
    stop = page.get_by_role("button", name="停止生成", exact=True)
    stop.wait_for()
    page.screenshot(path=str(output / "motion-grounded-validating.png"))
    deadline = monotonic() + 120
    while stop.is_visible():
        assert monotonic() < deadline, "Grounded answer did not finish within the QA deadline"
        assert not page.locator(".st-key-stream_body").count()
        page.wait_for_timeout(50)
    source = page.locator('[class*="st-key-answer_body_"]').get_by_text(
        "资料保管期限为三年。", exact=False
    )
    source.wait_for()
    page.get_by_role("button", name="停止生成", exact=True).wait_for(state="hidden")
    page.get_by_text("知识库回答", exact=True).wait_for()
    page.get_by_text("查看 1 条引用来源", exact=True).click()
    page.get_by_text("motion-qa-source.txt", exact=True).wait_for()
    page.locator('[data-testid="stExpander"]').evaluate(
        "e=>Promise.all(e.getAnimations({subtree:true}).map(a=>a.finished.catch(()=>{})))"
    )
    filename = page.get_by_text("motion-qa-source.txt", exact=True)
    filename.scroll_into_view_if_needed()
    expander_bounds = page.locator('[data-testid="stExpander"]').bounding_box()
    source_bounds = filename.bounding_box()
    assert source_bounds["y"] + source_bounds["height"] <= (
        expander_bounds["y"] + expander_bounds["height"]
    )
    page.screenshot(path=str(output / "motion-grounded-citations.png"))
    results.append({"grounded_buffer_then_validated_sources": "passed"})
    page.get_by_role("button", name="新建会话", exact=True).click()
    page.get_by_text("今天，想弄清楚什么？", exact=True).wait_for()
    question.fill("资料保管期限是多久？ invalid_citation")
    question.press("Enter")
    page.get_by_text("回答包含无效引用", exact=False).wait_for()
    assert not page.locator(".st-key-stream_body").count()
    assert not page.locator('[class*="st-key-answer_body_"]').count()
    page.screenshot(path=str(output / "motion-invalid-citation.png"))
    results.append({"invalid_citation_never_displayed": "passed"})
    browser.close()

print(json.dumps(results, indent=2))
