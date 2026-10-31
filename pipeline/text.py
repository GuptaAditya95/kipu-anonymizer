"""Text utilities shared by every stage.

Status: IMPLEMENTED.
- `norm` / `contains`: a port of the judge's normalization in scripts/validar.mjs
  (NFKC, confusables, invisible chars, odd spaces, accents, casefold, digit separators).
  The leak sweep and the tests use these, so "leak" means exactly what the judge means.
- `fold`: lowercase without accents (matching key).
- `slug`, `style_like`: produce replacements in the same form as the original span.
"""
from __future__ import annotations

import re
import unicodedata

CONFUSABLES = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ј": "j",
    "ѕ": "s", "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Х": "X", "У": "Y", "І": "I",
    "Ј": "J", "Ѕ": "S", "В": "B", "Н": "H", "К": "K", "М": "M", "Т": "T", "ο": "o", "α": "a",
    "ρ": "p", "ν": "v", "ι": "i", "ℓ": "l", "ı": "i", "ɑ": "a",
}
_CONF_RX = re.compile("[" + "".join(CONFUSABLES) + "]")
_SPACES = re.compile(r"[\t\n\v\f\r \x1c-\x1f\x85\xa0  -     　]+")
_DIGIT_SEP = re.compile(r"(?<=\d)[\t\n\v\f\r \x1c-\x1f\x85\xa0  -     　.,'’`´·/\-–—_](?=\d)")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def clean(s: str) -> str:
    s = _CONF_RX.sub(lambda m: CONFUSABLES[m.group(0)], unicodedata.normalize("NFKC", s))
    s = "".join(c for c in s if unicodedata.category(c) != "Cf")
    return re.sub(r" {2,}", " ", _SPACES.sub(" ", s))


def norm(s: str) -> str:
    return strip_accents(clean(s)).lower().replace("ß", "ss")


def fold(s: str) -> str:
    """Matching key: lowercase, no accents, single spaces."""
    return re.sub(r"\s+", " ", strip_accents(s).lower()).strip()


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def contains(haystack: str, needle: str) -> bool:
    """Judge-equivalent containment (port of `contiene` in validar.mjs)."""
    if not needle:
        return False
    d = digits(needle)
    if d and len(d) >= 6 and len(d) >= 0.6 * len(needle) and not re.search(r"[a-zA-Z]", needle):
        compact = _DIGIT_SEP.sub("", clean(haystack))
        cands = {d} | ({d[2:]} if len(d) == 12 and d.startswith("57") else set())
        return any(re.search(rf"(?<!\d)(?:57)?{re.escape(x)}(?!\d)", compact) for x in cands)
    h, n = norm(haystack), norm(needle)
    if re.fullmatch(r"[\w .'_-]+", n) and re.search(r"[a-z]", n):
        if re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", h):
            return True
        words = n.split()
        if len(words) >= 2 and all(len(w) >= 3 for w in words):
            return bool(re.search(r"(?<![a-z0-9])" + r"\s*".join(map(re.escape, words)) + r"(?![a-z0-9])", h))
        return False
    return n in h


def slug(name: str, sep: str = "_") -> str:
    return sep.join(re.findall(r"[a-z0-9]+", fold(name)))


def has_accents(s: str) -> bool:
    return strip_accents(s) != s


def style_like(original: str, replacement: str) -> str:
    """Render `replacement` in the style of `original` (CAPS, accentless, lowercase, slug)."""
    if "_" in original and " " not in original:
        return slug(replacement, "_")
    if "-" in original and " " not in original and original.lower() == original:
        return slug(replacement, "-")
    letters = [c for c in original if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(letters) > 1:
        rep = replacement.upper()
        return strip_accents(rep) if not has_accents(original) else rep
    if letters and all(c.islower() for c in letters):
        rep = replacement.lower()
        return strip_accents(rep) if not has_accents(original) else rep
    return replacement


def word_spans(text: str, phrase_fold: str):
    """Yield (start, end) of `phrase_fold` (already folded) in `text`, accent/case-insensitive,
    whole words, tolerant to multiple spaces. Offsets refer to the original text."""
    if not phrase_fold:
        return
    # folding keeps length 1:1 for Latin text (NFD then drop marks per char), so compute per char
    folded = "".join(strip_accents(c).lower() or c for c in text)
    if len(folded) != len(text):  # rare multi-char expansions: fall back to char-by-char map
        folded = "".join((strip_accents(c).lower() or c)[:1] for c in text)
    pat = r"(?<![\w])" + r"\s+".join(map(re.escape, phrase_fold.split())) + r"(?![\w])"
    for m in re.finditer(pat, folded):
        yield m.start(), m.end()


class Haystack:
    """Precomputed normalizations of a text, for many `contains` checks on the same text."""

    def __init__(self, text: str):
        self.norm = norm(text)
        self.compact = _DIGIT_SEP.sub("", clean(text))

    def contains(self, needle: str) -> bool:
        if not needle:
            return False
        d = digits(needle)
        if d and len(d) >= 6 and len(d) >= 0.6 * len(needle) and not re.search(r"[a-zA-Z]", needle):
            cands = {d} | ({d[2:]} if len(d) == 12 and d.startswith("57") else set())
            return any(x in self.compact and re.search(rf"(?<!\d)(?:57)?{re.escape(x)}(?!\d)", self.compact)
                       for x in cands)
        n = norm(needle)
        if re.fullmatch(r"[\w .'_-]+", n) and re.search(r"[a-z]", n):
            words = n.split()
            if words and words[0] not in self.norm:
                return False
            if re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", self.norm):
                return True
            if len(words) >= 2 and all(len(w) >= 3 for w in words):
                return bool(re.search(r"(?<![a-z0-9])" + r"\s*".join(map(re.escape, words)) + r"(?![a-z0-9])", self.norm))
            return False
        return n in self.norm
