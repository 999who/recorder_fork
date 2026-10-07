"""
Jedno nagranie na dzień: po Stop kolejny Start tego samego dnia kontynuuje dotychczasowe nagranie
(ten sam plik transkrypcji, sesji JSON i WAV, ten sam rekord spotkania w chmurze) zamiast zaczynać nowe.
"""
import os
import re
from datetime import date, datetime
from typing import Optional

_TXT_RE = re.compile(r"^transkrypcja_(\d{8}_\d{6}(?:_\d+)?)\.txt$")


def find_day_record(transcriptions_dir: str, day: date) -> Optional[str]:
    """Znacznik czasu (np. '20261007_091502_123456') najnowszego nagrania z danego dnia albo None."""
    prefix = day.strftime("%Y%m%d") + "_"
    best = None
    try:
        names = os.listdir(transcriptions_dir)
    except OSError:
        return None
    for name in names:
        m = _TXT_RE.match(name)
        if m and m.group(1).startswith(prefix) and (best is None or m.group(1) > best):
            best = m.group(1)
    return best


def timestamp_to_datetime(ts: str) -> Optional[datetime]:
    for fmt in ("%Y%m%d_%H%M%S_%f", "%Y%m%d_%H%M%S"):
        try:
            return datetime.strptime(ts, fmt)
        except (TypeError, ValueError):
            continue
    return None
