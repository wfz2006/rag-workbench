"""Explicit synonyms for the three optional observation classifications."""

# An upper limit ("up to") or an unqualified result ("obtained") does not establish a maximum.
MATERIAL_TERMS = {
    "carrier": {
        "hole": ("hole", "holes", "空穴"),
        "electron": ("electron", "electrons", "电子"),
    },
    "regime": {
        "saturation": ("saturation", "saturation regime", "saturation region", "饱和区"),
        "linear": (
            "linear", "linear regime", "linear region", "ohmic regime", "ohmic region", "线性区",
        ),
    },
    "statistic": {
        "max": ("max", "maximum", "highest", "最大", "最高"),
        "mean": ("mean", "avg", "average", "均值", "平均"),
        "min": ("min", "minimum", "lowest", "最小", "最低"),
        "typical": ("typical", "典型"),
    },
}


def standard_material_term(field: str, value: str) -> str | None:
    choices = MATERIAL_TERMS.get(field)
    if choices is None:
        return None
    clean = " ".join(value.split()).casefold()
    if field == "statistic" and clean in {"max.", "min.", "avg."}:
        clean = clean[:-1]
    return next((term for term, aliases in choices.items() if clean in aliases), None)
