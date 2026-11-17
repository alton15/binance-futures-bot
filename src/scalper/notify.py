"""Discord webhook notifications (best effort, never blocks trading)."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)
TIMEOUT_S = 10.0


class Notifier:
    def __init__(self, alerts_url: str, reports_url: str, tag: str) -> None:
        self._alerts = alerts_url
        self._reports = reports_url or alerts_url
        self._tag = tag
        self._client = httpx.AsyncClient(timeout=TIMEOUT_S)

    async def close(self) -> None:
        await self._client.aclose()

    async def alert(self, text: str) -> None:
        await self._post(self._alerts, text)

    async def report(self, text: str) -> None:
        await self._post(self._reports, text)

    async def _post(self, url: str, text: str) -> None:
        if not url:
            return
        try:
            r = await self._client.post(url, json={"content": f"[{self._tag}] {text}"[:1900]})
            if r.status_code >= 400:
                logger.warning("discord webhook HTTP %d", r.status_code)
        except httpx.HTTPError as e:
            logger.warning("discord webhook failed: %s", e)
