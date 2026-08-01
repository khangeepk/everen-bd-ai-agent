"""PageSpeed Insights (Lighthouse) HTTP client.

Uses PSI API v5 ``runPagespeed``, which returns Lighthouse lab data for the
``performance``, ``accessibility``, ``best-practices``, and ``seo`` categories.

Response interpretation lives in :mod:`app.services.pagespeed_parsing` so it can
be tested without a network stack; this module is transport only.

Note: Google has announced that real-world Chrome UX Report data will be removed
from this API. This client reads only ``lighthouseResult``, so that removal does
not affect it. If field data is wanted later, call the CrUX API separately
rather than depending on PSI's ``loadingExperience``.

Reference: https://developers.google.com/speed/docs/insights/v5/get-started
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings
from app.services.pagespeed_parsing import (
    PSI_CATEGORIES,
    PageSpeedError,
    PageSpeedResult,
    Strategy,
    findings_from_pagespeed,
    parse_pagespeed,
)

logger = logging.getLogger(__name__)

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

__all__ = [
    "PSI_ENDPOINT",
    "PageSpeedClient",
    "PageSpeedError",
    "PageSpeedResult",
    "Strategy",
    "findings_from_pagespeed",
    "parse_pagespeed",
]


class PageSpeedClient:
    """PageSpeed Insights API v5 client."""

    def __init__(self, api_key: str | None = None, timeout_seconds: float = 60.0) -> None:
        """Initialize the client.

        Args:
            api_key: PSI API key. Defaults to settings.
            timeout_seconds: Per-request timeout. Lighthouse runs are slow;
                60 seconds is a realistic floor.
        """
        self._api_key = api_key or settings.pagespeed_api_key
        self._timeout = timeout_seconds

    async def run(self, url: str, strategy: Strategy = Strategy.MOBILE) -> PageSpeedResult:
        """Run PageSpeed Insights against a URL.

        Args:
            url: The page to analyse.
            strategy: Device profile to emulate.

        Returns:
            The parsed result.

        Raises:
            PageSpeedError: On transport failure, a non-2xx response, or an
                unparseable payload.
        """
        params: list[tuple[str, str]] = [
            ("url", url),
            ("strategy", strategy.value),
            ("key", self._api_key),
        ]
        params.extend(("category", category) for category in PSI_CATEGORIES)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(PSI_ENDPOINT, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "PageSpeed Insights returned an error",
                extra={"status": exc.response.status_code, "url": url},
            )
            raise PageSpeedError(
                f"PageSpeed Insights error {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            logger.exception("PageSpeed Insights unreachable", extra={"url": url})
            raise PageSpeedError(f"PageSpeed Insights request failed: {exc}") from exc

        result = parse_pagespeed(payload, strategy)
        logger.info(
            "PageSpeed run complete",
            extra={
                "url": url,
                "strategy": strategy.value,
                "categories": len(result.category_scores),
                "failed_audits": len(result.failed_audits),
            },
        )
        return result
