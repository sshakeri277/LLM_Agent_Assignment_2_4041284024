"""Reading the input file: validation, privacy filtering and the brief every agent receives."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# Child fields that identify a real child. They are dropped before anything reaches a model or a log
# (assignment §9: no surname, address, phone, school, photo or other sensitive data).
_PRIVATE_PATTERNS = re.compile(
    r"last_?name|family|surname|address|phone|mobile|tel\b|school|photo|image|picture|avatar|email|"
    r"national|passport|birth|parent|guardian|city|postal|zip|location|ssn|id_number",
    re.IGNORECASE)
# Words that make a *request* unsafe for a children's story (whole words, so «کشتی» or «خونسردی» do not match).
# A hit does not refuse the request: the brief tells the Author to rewrite that item into a safe equivalent.
_UNSAFE_WORDS = [
    "کشتن", "کشته", "بکشد", "بکشند", "بکشیم", "کشت", "قتل", "خون", "خونین", "خونریزی", "سلاح", "تفنگ", "اسلحه",
    "شمشیر", "نبرد", "جنگ", "جنگیدن", "بجنگد", "حمله", "زخمی", "مرگ", "مردن", "بمیرد", "مرده", "ترسناک",
    "وحشتناک", "وحشت", "شکنجه", "انفجار", "بمب", "آدم‌ربایی", "ربودن", "خودکشی", "kill", "blood", "bloody",
    "weapon", "gun", "sword", "war", "battle", "fight", "death", "die", "scary", "horror", "murder",
]
_UNSAFE = re.compile(r"(?<![\w\u200c])(?:" + "|".join(map(re.escape, _UNSAFE_WORDS)) + r")(?![\w\u200c])",
                     re.IGNORECASE)
_KNOWN_CHILD = {"first_name", "name", "age", "interests", "favorite_character", "reading_level", "gender",
                "personality", "favorite_color", "favorite_things", "pronouns"}
_KNOWN_REQUEST = {"theme", "moral", "setting", "target_word_count", "word_count", "length", "must_include",
                  "avoid"}


class InputError(ValueError):
    pass


@dataclass
class StoryRequest:
    story_id: str
    first_name: str
    age: Optional[int]
    interests: List[str]
    favorite_character: str
    reading_level: str
    theme: str
    moral: str
    setting: str
    length: Any
    must_include: List[str]
    avoid: List[str]
    extra_child: Dict[str, Any] = field(default_factory=dict)
    extra_request: Dict[str, Any] = field(default_factory=dict)
    removed_fields: List[str] = field(default_factory=list)
    unsafe_items: List[str] = field(default_factory=list)   # requested items that need a safe rewrite


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "، ".join(_text(v) for v in value if _text(v))
    return str(value).strip()


def _list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [s for s in (_text(v) for v in value) if s]
    return [s.strip() for s in re.split(r"[,،;؛\n]", str(value)) if s.strip()]


def _age(value: Any) -> Optional[int]:
    if value is None:
        return None
    m = re.search(r"\d+", str(value).translate(_FA_DIGITS))
    return int(m.group()) if m else None


def parse_request(obj: Any, index: int = 1) -> StoryRequest:
    if not isinstance(obj, dict):
        raise InputError(f"story #{index} is not a JSON object")
    child = obj.get("child") if isinstance(obj.get("child"), dict) else {}
    req = obj.get("story_request") if isinstance(obj.get("story_request"), dict) else {}
    removed: List[str] = []
    clean_child: Dict[str, Any] = {}
    for key, value in child.items():
        if key not in _KNOWN_CHILD and _PRIVATE_PATTERNS.search(key):
            removed.append(f"child.{key}")
        else:
            clean_child[key] = value
    length = next((req[k] for k in ("target_word_count", "word_count", "length") if req.get(k) not in (None, "")),
                  None)
    requested = [_text(req.get(k)) for k in ("theme", "moral", "setting")] + _list(req.get("must_include")) + [
        _text(v) for k, v in req.items() if k not in _KNOWN_REQUEST] + [_text(clean_child.get("favorite_character"))]
    return StoryRequest(
        story_id=_text(obj.get("story_id")) or f"story_{index}",
        first_name=_text(clean_child.get("first_name") or clean_child.get("name")),
        age=_age(clean_child.get("age")),
        interests=_list(clean_child.get("interests")),
        favorite_character=_text(clean_child.get("favorite_character")),
        reading_level=_text(clean_child.get("reading_level")),
        theme=_text(req.get("theme")),
        moral=_text(req.get("moral")),
        setting=_text(req.get("setting")),
        length=length,
        must_include=_list(req.get("must_include")),
        avoid=_list(req.get("avoid")),
        extra_child={k: v for k, v in clean_child.items() if k not in _KNOWN_CHILD},
        extra_request={k: v for k, v in req.items() if k not in _KNOWN_REQUEST and v not in (None, "", [])},
        removed_fields=removed,
        unsafe_items=[item for item in requested if item and _UNSAFE.search(item)],
    )


def load_requests(path: str) -> Tuple[List[StoryRequest], bool]:
    """Return the requests and whether the input was a list (the output mirrors that shape)."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc
    if isinstance(data, dict):
        for key in ("stories", "requests", "items", "tasks"):
            if isinstance(data.get(key), list):
                return [parse_request(o, i + 1) for i, o in enumerate(data[key])], True
        return [parse_request(data)], False
    if isinstance(data, list):
        return [parse_request(o, i + 1) for i, o in enumerate(data)], True
    raise InputError("the input must be a JSON object or a list of objects")


def age_range(age: Optional[int]) -> str:
    if age is None:
        return ""
    return f"{max(3, age - 1)}-{age + 1}"


def word_range(r: StoryRequest) -> Tuple[int, int]:
    """Target length as (min, max) words; a single target gets roughly ±11 % (450 -> 400-500)."""
    value = r.length
    nums: List[int] = []
    if isinstance(value, dict):
        nums = [int(value[k]) for k in ("min", "max") if str(value.get(k, "")).isdigit()]
    elif isinstance(value, (list, tuple)):
        nums = [int(x) for x in value if str(x).isdigit()]
    elif value is not None:
        nums = [int(x) for x in re.findall(r"\d+", str(value).translate(_FA_DIGITS))]
    if len(nums) >= 2:
        return min(nums[:2]), max(nums[:2])
    if len(nums) == 1 and nums[0] > 0:
        t = nums[0]
        return (t * 89) // 100, -((-t * 111) // 100)
    age = r.age
    if age is None:
        return 300, 450
    if age <= 5:
        return 200, 250
    if age <= 7:
        return 300, 450
    if age <= 10:
        return 450, 600
    return 500, 700


def render_brief(r: StoryRequest) -> str:
    """The requirements every agent sees, one per line (values stay in the user's language)."""
    lo, hi = word_range(r)
    free = "none given — free choice, not a requirement"
    lines = [
        f"- Child's first name: {r.first_name or 'none given — use a warm generic hero'}",
        f"- Child's age: {r.age if r.age is not None else 'none given — write for ages 6-8'}"
        + (f" (audience age range {age_range(r.age)})" if r.age is not None else ""),
        f"- Child's interests: {', '.join(r.interests) or free}",
        f"- Favourite character: {r.favorite_character or free}",
        f"- Reading level: {r.reading_level or 'none given — follow the age'}",
        f"- Theme: {r.theme or 'none given — free choice, age-appropriate'}",
        f"- Moral / message: {r.moral or 'none given — choose one that fits the theme'}",
        f"- Setting: {r.setting or 'none given — free choice that fits the interests'}",
        f"- Length: {lo}-{hi} words (Persian words)",
        f"- Must include: {'; '.join(r.must_include) or 'nothing extra'}",
        f"- Must avoid: {'; '.join(r.avoid) or 'nothing extra beyond the general child-safety rules'}",
    ]
    if r.unsafe_items:
        lines.append("- SAFETY NOTE: these requested items are not suitable for a children's story as written: "
                     + "; ".join(f"«{i}»" for i in r.unsafe_items) + ". Keep the child-friendly wish behind them "
                     "but rewrite them into a safe equivalent (no fighting, weapons, injury, death or fear: e.g. a "
                     "conflict becomes a misunderstanding solved by talking, a contest, a puzzle or a kind act).")
    for key, value in list(r.extra_child.items()) + list(r.extra_request.items()):
        lines.append(f"- {key.replace('_', ' ')}: {_text(value) if not isinstance(value, dict) else json.dumps(value, ensure_ascii=False)}")
    return "\n".join(lines)
