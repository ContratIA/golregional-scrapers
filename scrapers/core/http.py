"""Cliente HTTP educado: rate limit por dominio, robots.txt, reintentos con backoff y
caché por hash de contenido."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

CONTACT_EMAIL = "eloymuros@gmail.com"
BOT_TOKEN = "FutbolDirectoHoyBot"
USER_AGENT = f"{BOT_TOKEN}/1.0 (+https://futboldirectohoy.com/bot; mailto:{CONTACT_EMAIL})"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RobotsDisallowed(Exception):
    pass


class Robots:
    """robots.txt según RFC 9309: gana la regla más específica (más larga) y, a igualdad,
    Allow. `urllib.robotparser` aplica la primera que coincide, y con el robots.txt de la
    RFFM (`Allow: /` antes de los `Disallow`) permitiría rutas prohibidas."""

    def __init__(self, text: str, agent: str = BOT_TOKEN):
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (p.strip() for p in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if rules:
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.lower())
            elif key in ("allow", "disallow") and agents and value:
                rules.append((key == "allow", value))
        if agents:
            groups.append((agents, rules))

        token = agent.lower()
        specific = [r for a, r in groups if any(x != "*" and x in token for x in a)]
        wildcard = [r for a, r in groups if "*" in a]
        chosen = specific or wildcard
        self.rules = [rule for group in chosen for rule in group]

    @staticmethod
    def _matches(pattern: str, path: str) -> bool:
        anchored = pattern.endswith("$")
        body = re.escape(pattern.rstrip("$")).replace(r"\*", ".*")
        return re.match(body + ("$" if anchored else ""), path) is not None

    def can_fetch(self, url: str) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        if parts.path == "/robots.txt":
            return True
        best: tuple[int, bool] | None = None
        for allow, pattern in self.rules:
            if self._matches(pattern, path):
                candidate = (len(pattern), allow)
                if best is None or candidate > best:
                    best = candidate
        return True if best is None else best[1]


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    content_hash: str
    changed: bool  # False si el contenido es idéntico a la última descarga


@dataclass
class PoliteClient:
    min_interval: float = 3.0
    max_retries: int = 4
    backoff_base: float = 2.0
    timeout: float = 30.0
    cache_path: Path | None = None
    transport: httpx.BaseTransport | None = None
    sleep: callable = time.sleep
    clock: callable = time.monotonic
    requests_made: int = 0
    _last_request: dict[str, float] = field(default_factory=dict)
    _robots: dict[str, Robots] = field(default_factory=dict)
    _hashes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "es-ES,es;q=0.9"},
            timeout=self.timeout,
            follow_redirects=True,
            transport=self.transport,
        )
        if self.cache_path and self.cache_path.exists():
            self._hashes = json.loads(self.cache_path.read_text(encoding="utf-8"))

    def close(self) -> None:
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._hashes, indent=0), encoding="utf-8")
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _wait_turn(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            remaining = self.min_interval - (self.clock() - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request[host] = self.clock()

    def _raw_get(self, url: str) -> httpx.Response:
        host = urlsplit(url).netloc
        attempt = 0
        while True:
            self._wait_turn(host)
            self.requests_made += 1
            try:
                resp = self._client.get(url)
                if resp.status_code not in RETRYABLE_STATUS:
                    return resp
                retry_after = resp.headers.get("Retry-After")
                error: Exception = httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                retry_after = None
                error = exc
            attempt += 1
            if attempt > self.max_retries:
                raise error
            delay = float(retry_after) if retry_after and retry_after.isdigit() else self.backoff_base**attempt
            delay += random.uniform(0, 1)
            log.warning("reintento %s/%s para %s en %.1fs (%s)", attempt, self.max_retries, url, delay, error)
            self.sleep(delay)

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        robots = self._robots.get(origin)
        if robots is None:
            resp = self._raw_get(f"{origin}/robots.txt")
            # 4xx = sin restricciones (RFC 9309 §2.3.1.3)
            robots = Robots("" if resp.status_code >= 400 else resp.text)
            self._robots[origin] = robots
        return robots.can_fetch(url)

    def get(self, url: str) -> FetchResult:
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        resp = self._raw_get(url)
        resp.raise_for_status()
        text = resp.text
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        changed = self._hashes.get(url) != digest
        self._hashes[url] = digest
        return FetchResult(url=url, status=resp.status_code, text=text, content_hash=digest, changed=changed)

    def get_bytes(self, url: str) -> tuple[bytes, str]:
        """Binario (escudos) con las mismas reglas: robots.txt, 1 petición cada 3 s y reintentos."""
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        resp = self._raw_get(url)
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "")

    def get_json(self, url: str):
        return json.loads(self.get(url).text)
