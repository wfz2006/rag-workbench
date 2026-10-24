import re


def _pages(chunk):
    return [int(value) for value in re.findall(r"〔p(\d{4,6})〕", chunk.text)]


def test_spoiler_limit_excludes_later_page_chunks(knowledge):
    knowledge.settings.chunk_size = 50
    knowledge.settings.chunk_overlap = 10
    body = "甲。" * 40 + "\n〔p0002〕\n" + "乙。" * 40
    knowledge.ingest("book.md", ("〔p0001〕\n" + body).encode())
    assert {chunk.page for chunk in knowledge.repo.chunks() if chunk.page} == {1, 2}
    knowledge.settings.spoiler_max_page = None
    unfiltered = knowledge.retrieve("乙")
    assert any(2 in _pages(result.chunk) for result in unfiltered)
    knowledge.settings.spoiler_max_page = 1
    filtered = knowledge.retrieve("乙")
    assert all(max(_pages(result.chunk), default=0) <= 1 for result in filtered)
