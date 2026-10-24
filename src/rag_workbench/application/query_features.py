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
