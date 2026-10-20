"""HTTP-клиент rasp.dmami.ru с кэшем сырых ответов, ретраями и паузами."""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .parser import ScheduleUnavailable

logger = logging.getLogger(__name__)

URL = "https://rasp.dmami.ru/site/group?group={group}&session={session}"
HEADERS = {
    "Referer": "https://rasp.dmami.ru/",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                   "(KHTML, like Gecko) Version/18.6 Safari/605.1.15"),
    "Accept": "*/*",
}
TIMEOUT = 15
ATTEMPTS = 3


class FetchError(Exception):
    """Сеть/сервер недоступны после всех попыток."""


def cache_path(raw_dir: Path, session: int, group: str) -> Path:
    return raw_dir / str(session) / f"{group.replace('/', '_')}.json"


def _get(url: str) -> bytes:
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code != 429 and exc.code < 500:
                raise FetchError(f"HTTP {exc.code}") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        if attempt < ATTEMPTS - 1:
            time.sleep(2 ** attempt)
    raise FetchError(f"сеть недоступна: {last}")


def fetch_group(group: str, session: int, raw_dir: Path, refresh: bool = False,
                pause: tuple[float, float] = (0.2, 0.5)):
    """Возвращает (payload, from_cache). Кэш хранит только осмысленные ответы."""
    path = cache_path(raw_dir, session, group)
    if path.exists() and not refresh:
        try:
            return json.loads(path.read_text(encoding="utf-8")), True
        except (OSError, ValueError):
            logger.warning("Битый кэш %s, запрашиваю заново", path)

    time.sleep(random.uniform(*pause))
    body = _get(URL.format(group=urllib.parse.quote(group), session=session))
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise ScheduleUnavailable("ответ не JSON") from exc
    if isinstance(payload, dict) and (
            payload.get("status") == "ok"
            or "Не нашлось расписание" in str(payload.get("message"))):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    return payload, False
