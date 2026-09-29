"""
Autokorekty po rozpoznaniu: tabela "błędnie -> poprawnie" zastępująca initial_prompt,
którego Parakeet nie posiada.
"""
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

Pair = Tuple[str, str]


def normalize_pairs(raw: Any) -> List[Pair]:
    """Zamienia dane z ustawień (lista list / słowników) na listę par (błędnie, poprawnie), bez pustych."""
    pairs: List[Pair] = []
    for item in raw or []:
        if isinstance(item, dict):
            src, dst = item.get("from", ""), item.get("to", "")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            src, dst = item
        else:
            continue
        src, dst = str(src).strip(), str(dst).strip()
        if src and dst:
            pairs.append((src, dst))
    return pairs


def _pattern(src: str) -> "re.Pattern[str]":
    parts = [re.escape(p) for p in src.split()]
    return re.compile(r"(?<![\w])" + r"\s+".join(parts) + r"(?![\w])", re.IGNORECASE)


def apply_text_replacements(text: str, pairs: Optional[Sequence[Pair]] = None) -> str:
    """Zamienia całe słowa i frazy (bez rozróżniania wielkości liter). Dłuższe frazy mają pierwszeństwo."""
    if pairs is None:
        pairs = load_configured_pairs()
    if not text or not pairs:
        return text
    for src, dst in sorted(pairs, key=lambda p: len(p[0]), reverse=True):
        text = _pattern(src).sub(lambda m, d=dst: d, text)
    return text


def apply_word_replacements(words: List[Dict[str, Any]], pairs: Optional[Sequence[Pair]] = None) -> List[Dict[str, Any]]:
    """
    Stosuje autokorekty na liście słów ze znacznikami czasu. Fraza wielowyrazowa jest zastępowana
    jednym słowem obejmującym czas od pierwszego do ostatniego słowa frazy.
    """
    if pairs is None:
        pairs = load_configured_pairs()
    if not words or not pairs:
        return words

    def norm(w: Dict[str, Any]) -> str:
        return re.sub(r"[^\w]", "", str(w.get("word", ""))).lower()

    rules = sorted(
        [([re.sub(r"[^\w]", "", t).lower() for t in src.split()], dst) for src, dst in pairs],
        key=lambda r: len(r[0]), reverse=True,
    )
    out: List[Dict[str, Any]] = []
    i = 0
    while i < len(words):
        for toks, dst in rules:
            n = len(toks)
            if n and i + n <= len(words) and all(norm(words[i + k]) == toks[k] for k in range(n)):
                first, last = words[i], words[i + n - 1]
                orig = str(last.get("word", ""))
                trail = re.search(r"[^\w\s]+$", orig)          # zachowaj interpunkcję końcową
                lead = " " if str(first.get("word", "")).startswith(" ") else ""
                out.append({**first, "word": lead + dst + (trail.group(0) if trail else ""), "end": last.get("end", first.get("end"))})
                i += n
                break
        else:
            out.append(words[i])
            i += 1
    return out


def load_configured_pairs() -> List[Pair]:
    """Pary z user_settings.json (klucz custom_replacements)."""
    from recorder.config import load_user_settings
    return normalize_pairs(load_user_settings().get("custom_replacements", []))
