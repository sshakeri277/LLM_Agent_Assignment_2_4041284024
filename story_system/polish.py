"""Deterministic Persian typography for the final text (no meaning is changed)."""
from __future__ import annotations

import re

_ARABIC = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"})


def _quotes(line: str) -> str:
    line = re.sub(r"«([^«»\"]*)\"", r"«\1»", line)   # «…"  ->  «…»
    line = re.sub(r"\"([^«»\"]*)»", r"«\1»", line)   # "…»  ->  «…»
    if line.count('"') % 2:                          # unbalanced: leave it rather than guess
        return line
    out, opening = [], True
    for ch in line:
        if ch == '"':
            out.append("«" if opening else "»")
            opening = not opening
        else:
            out.append(ch)
    return "".join(out)


def polish_persian(text: str) -> str:
    """Persian quotation marks «», Persian ی/ک, single spaces, one blank line between paragraphs."""
    text = (text or "").translate(_ARABIC).replace("\r\n", "\n")
    paragraphs = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t ]+", " ", line).strip()
        if line:
            paragraphs.append(_quotes(line))
    return "\n\n".join(paragraphs)
