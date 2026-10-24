import json
import re
from dataclasses import dataclass
from itertools import islice, product, zip_longest
from pathlib import Path

from rag_workbench.application.teaching import followup_core, is_followup
from rag_workbench.domain.errors import AppError

SECTIONS = {"aliases", "kinship", "relations"}
MAX_FILE_BYTES = 64 * 1024
MAX_VALUES = 50
MAX_RULES = 100
MAX_KEY_CHARS = 40
MAX_VALUE_CHARS = 80
MAX_TERMS = 40
MAX_TEXT_CHARS = 600


@dataclass(frozen=True)
class ExpandedQuery:
    text: str
    terms: tuple[str, ...]


def _named_subject(text: str) -> str | None:
    """Extract only explicit, simple subjects; never guess arbitrary named entities."""
    clean = text.strip().rstrip("？?！!。")
    switched = re.fullmatch(r"那么?(.{1,12})呢", clean)
    if switched:
        return switched[1].strip()
    if is_followup(clean):
        return None
    introduced = re.sub(r"^(?:请)?(?:介绍|讲讲|讲述)(?:一下)?", "", clean)
    if introduced != clean:
        subject = re.split(r"的(?:生平|经历|故事)", introduced)[0].strip()
        return subject if 0 < len(subject) <= 24 else None
    match = re.match(
        r"^([^，。？！!?\n]{1,24}?)(?:是(?:怎么|如何|谁|什么)|为什么|怎么|怎样|如何|何时|什么时候|在哪里|的(?:生平|经历))",
        clean,
    )
    return match[1].strip() if match else None


def coverage_focus(question: str, context: str | None = None) -> str:
    """Use the nearest explicit subject, not old question predicates, for lexical recall."""
    clean = followup_core(question).rstrip("？?！!。")
    switched = re.fullmatch(r"那么?(.{1,12})呢", clean)
    if switched:
        return switched[1].strip()
    if not context or not is_followup(question):
        return question
    history = context.split("\n")
    if history[-1].strip() == question.strip():
        history.pop()
    subject = None
    for previous in reversed(history):
        subject = _named_subject(previous)
        if subject or not is_followup(previous):
            break
    if not subject:
        return question
    # Pronoun and zero-subject follow-ups ("后来呢", "为什么这样") share the same
    # nearest explicit subject; only the pronoun itself is dropped.
    intent = re.sub(r"^[他她它](?:们)?", "", clean)
    intent = re.sub(r"^(?:(?:后来|之后|最终|接着|当时|怎么(?!样)|怎样|如何|为什么))+", "", intent)
    intent = re.sub(r"(?:的|呢|吗)$", "", intent)
    return f"{subject} {intent}".strip()


def coverage_queries(question: str, expansions: dict[str, object], limit: int = 40) -> list[str]:
    """Fair, bounded original-span substitutions and entity-constrained relation proposals."""
    if limit < 1:
        return []
    mappings = {}
    for section in ("aliases", "kinship"):
        mapping = expansions.get(section, {})
        if isinstance(mapping, dict):
            for key, values in mapping.items():
                mappings.setdefault(key, []).extend(values)
    occupied, groups = set(), []
    for key in sorted(mappings, key=lambda k: (-len(k), k)):
        spans = []
        for match in re.finditer(re.escape(key), question):
            positions = set(range(match.start(), match.end()))
            if not occupied.intersection(positions):
                spans.append((match.start(), match.end()))
                occupied.update(positions)
        if spans:
            groups.append((spans, list(dict.fromkeys(mappings[key]))))
    groups.sort(key=lambda group: group[0][0])

    def render(assignments):
        edits = [
            (start, end, value)
            for (spans, _), value in zip(groups, assignments, strict=True)
            if value is not None
            for start, end in spans
        ]
        text = question
        for start, end, value in sorted(edits, reverse=True):
            text = text[:start] + value + text[end:]
        return text

    pools = []
    for i, (_, values) in enumerate(groups):
        pool = []
        for value in values:
            assignments = [None] * len(groups)
            assignments[i] = value
            pool.append(render(assignments))
        pools.append(pool)
    relations = expansions.get("relations", [])
    if isinstance(relations, list):
        for rule in relations:
            people = [p for p in rule["people"] if p in question]
            if people and any(cue in question for cue in rule["cues"]):
                pools.append(
                    [f"{person} {related}" for related in rule["related"] for person in people]
                )
    queries = [question]
    single_budget = min(limit, max(len(pools) + 1, limit // 2))
    for row in zip_longest(*pools):
        for candidate in row:
            if candidate and candidate not in queries:
                queries.append(candidate)
                if len(queries) >= single_budget:
                    break
        if len(queries) >= single_budget:
            break
    if len(groups) > 1:
        for assignment in islice(product(*(values[:3] for _, values in groups)), limit * 4):
            candidate = render(assignment)
            if candidate not in queries and len(queries) < limit:
                queries.append(candidate)
    for row in zip_longest(*pools):
        for candidate in row:
            if candidate and candidate not in queries and len(queries) < limit:
                queries.append(candidate)
    return queries[:limit]


def load_retrieval_expansions(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    try:
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("文件过大")
        parsed = json.loads(data.decode("utf-8-sig"))
        return _validate(parsed)
    except AppError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise AppError(
            "invalid_retrieval_expansions",
            "查询扩展配置无效：请检查 UTF-8 JSON、字段名和词条长度。",
        ) from exc


def _validate(parsed: object) -> dict[str, object]:
    if not isinstance(parsed, dict):
        raise ValueError("顶层必须是对象")
    unknown = set(parsed) - SECTIONS
    if unknown:
        raise ValueError(f"未知字段：{sorted(unknown)}")
    result: dict[str, object] = {}
    for section, value in parsed.items():
        if section == "relations":
            result[section] = _validate_relations(value)
        else:
            result[section] = _validate_terms(section, value)
    return result


def _validate_terms(section: str, mapping: object) -> dict[str, list[str]]:
    if not isinstance(mapping, dict):
        raise ValueError(f"{section} 必须是对象")
    normalized = {}
    for key, values in mapping.items():
        if not isinstance(key, str) or not key.strip() or len(key) > MAX_KEY_CHARS:
            raise ValueError(f"{section} 的键无效")
        if not isinstance(values, list) or not values or len(values) > MAX_VALUES:
            raise ValueError(f"{section}.{key} 必须是非空词条数组")
        cleaned = []
        for value in values:
            if not isinstance(value, str) or not value.strip() or len(value) > MAX_VALUE_CHARS:
                raise ValueError(f"{section}.{key} 含无效词条")
            if value not in cleaned:
                cleaned.append(value)
        normalized[key] = cleaned
    return normalized


def _validate_relations(value: object) -> list[dict[str, list[str]]]:
    if not isinstance(value, list) or len(value) > MAX_RULES:
        raise ValueError("relations 必须是规则数组")
    rules = []
    for rule in value:
        if not isinstance(rule, dict) or set(rule) != {"people", "cues", "related"}:
            raise ValueError("relations 规则必须包含 people/cues/related")
        cleaned = {}
        for key in ("people", "cues", "related"):
            terms = rule[key]
            if not isinstance(terms, list) or not terms:
                raise ValueError(f"relations.{key} 必须是非空数组")
            values = []
            for term in terms:
                if not isinstance(term, str) or not term.strip() or len(term) > MAX_VALUE_CHARS:
                    raise ValueError(f"relations.{key} 含无效词条")
                if term not in values:
                    values.append(term)
            cleaned[key] = values
        rules.append(cleaned)
    return rules


def expand_query(
    question: str,
    expansions: dict[str, object],
    *,
    max_terms: int = MAX_TERMS,
) -> ExpandedQuery:
    terms: list[str] = []
    aliases = expansions.get("aliases", {})
    aliases = aliases if isinstance(aliases, dict) else {}
    relations = expansions.get("relations", [])
    if isinstance(relations, list):
        for rule in relations:
            if not isinstance(rule, dict):
                continue
            people = rule.get("people", [])
            cues = rule.get("cues", [])
            related = rule.get("related", [])
            if any(person in question for person in people) and any(
                cue in question for cue in cues
            ):
                for term in related:
                    for value in [term, *aliases.get(term, [])]:
                        if value not in question and value not in terms:
                            terms.append(value)
    for section in ("aliases", "kinship"):
        mapping = expansions.get(section, {})
        if not isinstance(mapping, dict):
            continue
        for key, values in mapping.items():
            if key not in question:
                continue
            for value in values:
                if value not in question and value not in terms:
                    terms.append(value)
    terms = terms[:max_terms]
    text = question
    for term in terms:
        if len(text) + 1 + len(term) > MAX_TEXT_CHARS:
            break
        text += " " + term
    return ExpandedQuery(text=text, terms=tuple(terms))
