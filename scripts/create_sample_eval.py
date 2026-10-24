"""Generate deliberately small, fictional labels for exercising the evaluator only."""

import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
examples = root / "examples"
cases = [
    {
        "id": "sample-retention",
        "question": "样例记录的保管期限是多少年？",
        "relevant_document_ids": [
            hashlib.sha256((examples / "sample.txt").read_bytes()).hexdigest()
        ],
        "expected_mode": "knowledge_base",
    },
    {
        "id": "sample-interface",
        "question": "示例工作台支持哪些主题？",
        "relevant_document_ids": [
            hashlib.sha256((examples / "interface.md").read_bytes()).hexdigest()
        ],
        "expected_mode": "knowledge_base",
    },
    {
        "id": "sample-unknown",
        "question": "资料负责人明天什么时候上班？",
        "relevant_document_ids": [],
        "expected_mode": "general",
    },
]
target = examples / "evaluation.jsonl"
target.write_text(
    "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n", encoding="utf-8"
)
print(target)
