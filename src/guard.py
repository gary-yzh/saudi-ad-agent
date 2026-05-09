"""Content guard for user input.

Two tiers of detection — both result in rejection of the user message
before we burn an LLM call, but we surface different category labels so
the UI can frame the rejection with appropriate language.

  hard_ban         — alcohol, pork, gambling, drugs, weapons. Cannot be
                     advertised in KSA, full stop. Mirrors the AR/EN
                     blocklist already in `src/nodes/guardrail.py` so the
                     same vocabulary is enforced at every stage.

  muslim_sensitive — nudity, sexual / suggestive content, romantic /
                     dating contexts, religious irreverence. These can
                     occasionally be navigated by skilled creatives but
                     are off-policy by default for a Saudi e-commerce
                     brand and we'd rather ask the user to rephrase.

The wider scan happens on the raw user text. The downstream `guardrail`
node (post-storyboard) re-scans the model's output with the same hard
list so a clever user can't bypass detection by paraphrasing.
"""
from __future__ import annotations

import re
from typing import Any

# Hard bans — outright illegal / forbidden in KSA advertising.
EN_HARD_BAN: tuple[str, ...] = (
    r"\balcohol(ic)?\b", r"\bbeer\b", r"\bwine\b", r"\bwhisky\b", r"\bwhiskey\b",
    r"\bvodka\b", r"\brum\b", r"\bgin\b", r"\bchampagne\b", r"\bcocktail\b",
    r"\bdrunk\b", r"\bbooze\b", r"\bliquor\b",
    r"\bpork\b", r"\bbacon\b", r"\bham\b(?!burger)", r"\bsausage\b", r"\blard\b",
    r"\bcasino\b", r"\bgambl(e|ing|er)\b", r"\bbet\b", r"\bbetting\b",
    r"\blotter(y|ies)\b", r"\bpoker\b",
    r"\bdrugs?\b", r"\bcannabis\b", r"\bmariju?ana\b", r"\bcocaine\b",
    r"\bheroin\b", r"\bweed\b(?!\s*killer)", r"\bopium\b",
    r"\bweapons?\b", r"\bguns?\b", r"\brifles?\b", r"\bpistols?\b",
)

# Muslim-sensitive — flagged but with a softer rejection message.
EN_MUSLIM_SENSITIVE: tuple[str, ...] = (
    # nudity / sexual / suggestive
    r"\bnud(e|ity)\b", r"\bnaked\b", r"\bbare(\s+chest|\s+skin|d)\b",
    r"\blingerie\b", r"\bunderwear\b", r"\bbikini\b", r"\bswimsuit\b",
    r"\bswimwear\b", r"\bsex(y|ual|ually)?\b", r"\berotic(a|al)?\b",
    r"\bintimate\b", r"\bsensu(al|ous)\b", r"\bseduc(e|tive|tion)\b",
    r"\bkiss(es|ing)?\b", r"\bmake[- ]?out\b", r"\bcleavage\b",
    # dating / romantic
    r"\bdating\b", r"\btinder\b", r"\bbumble\b", r"\bgirlfriend\b", r"\bboyfriend\b",
    r"\bromance\b", r"\bromantic\b",
    # religious / sectarian
    r"\bblasphem(y|ous)\b", r"\binfidel\b", r"\bheretic\b",
    r"\bsatan(ic|ism)?\b", r"\bdevil[- ]?worship\b",
    r"\bprophet\b",
)

AR_HARD_BAN: tuple[str, ...] = (
    "خمر", "خمور", "كحول", "نبيذ", "بيرة", "ويسكي", "فودكا", "شامبانيا",
    "خنزير", "لحم خنزير", "بيكون",
    "قمار", "كازينو", "رهان", "يانصيب", "بوكر",
    "مخدرات", "حشيش", "كوكايين", "هيروين",
)

AR_MUSLIM_SENSITIVE: tuple[str, ...] = (
    "عاري", "عارية", "تعرّي", "بكيني", "ملابس داخلية", "إغراء", "مغرية", "مثير",
    "قبلة", "قبلات", "تقبيل", "علاقة عاطفية", "صديق حميم", "صديقة حميمة",
    "كفر", "كافر", "ملحد", "إلحاد", "شيطان",
)


class UserInputViolation(ValueError):
    """Raised by `check_user_input` when the message hits the guard.

    `violations` is a list of dicts with keys: category, term, message.
    """

    def __init__(self, violations: list[dict[str, str]]):
        self.violations = violations
        head = ", ".join(v["term"] for v in violations[:3])
        super().__init__(f"Content guard rejected the input: {head}")


def _scan(text: str, patterns: tuple[str, ...], *, regex: bool) -> list[str]:
    hits: list[str] = []
    for p in patterns:
        if regex:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                hits.append(m.group(0))
        else:
            if p in text:
                hits.append(p)
    return hits


def check_user_input(text: str) -> list[dict[str, str]]:
    """Return a list of violations, empty if the text is clean."""
    if not text:
        return []
    out: list[dict[str, str]] = []

    for hit in _scan(text, EN_HARD_BAN, regex=True):
        out.append({
            "category": "hard_ban",
            "term": hit,
            "message": (
                f"\"{hit}\" can't appear in KSA advertising — alcohol, pork, "
                f"gambling, drugs and weapons are off-limits."
            ),
        })
    for hit in _scan(text, AR_HARD_BAN, regex=False):
        out.append({
            "category": "hard_ban",
            "term": hit,
            "message": (
                f"\"{hit}\" مما لا يجوز الإعلان عنه في السوق السعودي."
            ),
        })
    for hit in _scan(text, EN_MUSLIM_SENSITIVE, regex=True):
        out.append({
            "category": "muslim_sensitive",
            "term": hit,
            "message": (
                f"\"{hit}\" is sensitive in the KSA / Muslim market. "
                f"Try a more modest framing — e.g. focus on lifestyle, family, craft."
            ),
        })
    for hit in _scan(text, AR_MUSLIM_SENSITIVE, regex=False):
        out.append({
            "category": "muslim_sensitive",
            "term": hit,
            "message": (
                f"\"{hit}\" حساس في السوق المسلم. حاول صياغة أكثر احتشامًا."
            ),
        })

    # De-duplicate by term while preserving order
    seen = set()
    uniq: list[dict[str, str]] = []
    for v in out:
        key = (v["category"], v["term"].lower())
        if key not in seen:
            seen.add(key)
            uniq.append(v)
    return uniq


def assert_user_input_clean(text: str) -> None:
    """Helper: raise UserInputViolation if the input has any violation."""
    viols = check_user_input(text)
    if viols:
        raise UserInputViolation(viols)
