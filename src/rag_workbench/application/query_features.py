"""Query intent and literal-answer markers; no retrieval state."""

import re

TITLE_QUESTION = re.compile(r"哪[支首本]|曲名|篇名|书名|诗名|叫什么|什么名字|什么曲文")
QUOTE_QUESTION = re.compile(
    r"原话|原句|最后.{0,6}(?:说|喊|叫|道|问)|(?:说|喊|骂|叫)了什么|"
    r"(?:说|喊|骂)的.{0,12}(?:话|句子)是(?:什么|哪句)|哪句话|怎么说的|临终.{0,6}(?:说|喊|叫)"
)
MEANING_QUESTION = re.compile(
    r"表达(?:了)?什么(?:意思)?|是什么意思|说明什么|意味着什么|含义|为什么.{0,8}(?:喜欢|爱|反对|选择|看重)"
)
TITLE_MARK = re.compile(r"《[^》]+》")
QUOTE_MARK = re.compile(r"[“\"][^”\"]{2,}[”\"]")

# Only short ellipses whose intent can be filled from a previous materials question.
MEASUREMENT_PHRASE = re.compile(
    r"(?:(?:电子|空穴|平均|最高|最大|峰值|饱和|线性|场效应)\s*){0,5}(?:迁移率|开关比|阈值电压)"
    r"|\b(?:(?:electron|hole|average|mean|avg|max(?:imum)?|highest|peak|saturated|saturation|"
    r"linear|field[- ]effect)\s+){0,5}(?:mobilit(?:y|ies)|threshold voltage|vth|on\s*[/\-:]\s*off"
    r"(?:\s+ratio)?)\b",
    re.I,
)
_TEMPERATURE_FOLLOWUP = re.compile(
    r"[+−-]?\d+(?:\.\d+)?\s*(?:℃|°\s*C|摄氏度|度)\s*(?:下)?", re.I,
)
_COMPARISON_FOLLOWUP = re.compile(
    r"(?:(?:把)?它\s*)?(?:和|跟|与)\s*([a-z0-9][a-z0-9()\[\]_/−–—‐‑-]{0,47})\s*"
    r"(?:比(?:呢|怎么样)|[对相]比(?:一下|呢|怎么样)?)",
    re.I,
)


def material_followup(question: str) -> tuple[str, str] | None:
    """Return the one metric, temperature, or comparison target named in a short follow-up."""
    clean = question.strip().rstrip("？?。")
    comparison = _COMPARISON_FOLLOWUP.fullmatch(clean)
    if comparison:
        return "comparison", comparison[1]
    prefixed = re.sub(r"^(?:那么?|[它这]的)\s*", "", clean)
    core = re.sub(r"\s*(?:呢|怎么样|如何)$", "", prefixed)
    if core == clean:
        return None
    if MEASUREMENT_PHRASE.fullmatch(core):
        return "metric", core
    temperature = _TEMPERATURE_FOLLOWUP.fullmatch(core)
    if temperature:
        return "temperature", core
    return None


def question_type(question: str) -> str:
    if MEANING_QUESTION.search(question):
        return "meaning"
    if QUOTE_QUESTION.search(question):
        return "quote"
    if TITLE_QUESTION.search(question):
        return "title"
    return "general"


def contains_title(text: str) -> bool:
    return bool(TITLE_MARK.search(text))


def contains_quote(text: str) -> bool:
    return bool(QUOTE_MARK.search(text))
