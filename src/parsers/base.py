"""
Base parser abstract base class and execution statistics model.
Provides asynchronous streaming generator contract, connection pooling,
user-agent rotation, exponential retry backoff, and error containment.
"""

from __future__ import annotations

import abc
import asyncio
from datetime import datetime, timezone
import logging
import random
import time
from typing import Any, AsyncGenerator, Dict, List, Optional
import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.listing import RawListingPayload

logger = logging.getLogger(__name__)

USER_AGENTS: List[str] = [
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.6; rv:129.0) "
        "Gecko/20100101 Firefox/129.0"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.5 Safari/605.1.15"
    ),
]


class ParserRunStats(BaseModel):
    """
    Tracking model for parser execution telemetry.
    Supports both naming conventions (source / source_name, scanned / items_fetched,
    new / items_valid, errors / errors_count).
    """
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    source: str = Field(default="", description="Source identifier")
    source_name: str = Field(default="", description="Alias for source")
    status: str = Field(default="PENDING", description="Execution status: PENDING, SUCCESS, PARTIAL, FAILED, DEGRADED")

    scanned: int = Field(default=0, description="Total items scanned from source")
    items_fetched: int = Field(default=0, description="Alias for scanned")

    new: int = Field(default=0, description="Valid new items yielded")
    items_valid: int = Field(default=0, description="Alias for new")

    errors: int = Field(default=0, description="Errors encountered during run")
    errors_count: int = Field(default=0, description="Alias for errors")

    duration_seconds: float = Field(default=0.0, description="Elapsed runtime in seconds")
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = Field(default=None)

    @model_validator(mode="after")
    def sync_aliases(self) -> ParserRunStats:
        # Sync source name
        if not self.source and self.source_name:
            self.source = self.source_name
        elif not self.source_name and self.source:
            self.source_name = self.source

        # Sync scanned / fetched
        max_scanned = max(self.scanned, self.items_fetched)
        self.scanned = max_scanned
        self.items_fetched = max_scanned

        # Sync new / valid
        max_valid = max(self.new, self.items_valid)
        self.new = max_valid
        self.items_valid = max_valid

        # Sync errors
        max_errors = max(self.errors, self.errors_count)
        self.errors = max_errors
        self.errors_count = max_errors

        return self

    def start(self) -> None:
        """Mark start timestamp."""
        self.started_at = datetime.now(timezone.utc)
        self.status = "RUNNING"

    def finish(self, status: Optional[str] = None) -> None:
        """Mark finish timestamp and calculate duration."""
        self.finished_at = datetime.now(timezone.utc)
        if self.started_at:
            delta = (self.finished_at - self.started_at).total_seconds()
            self.duration_seconds = round(delta, 2)
        if status:
            self.status = status
        elif self.status == "RUNNING":
            if self.errors > 0 and self.new > 0:
                self.status = "PARTIAL"
            elif self.errors > 0 and self.new == 0:
                self.status = "FAILED"
            else:
                self.status = "SUCCESS"

    def record_fetched(self, count: int = 1) -> None:
        self.scanned += count
        self.items_fetched += count

    def record_valid(self, count: int = 1) -> None:
        self.new += count
        self.items_valid += count

    def record_error(self, count: int = 1) -> None:
        self.errors += count
        self.errors_count += count


class BaseParser(abc.ABC):
    """
    Abstract Base Class for all marketplace and social media scrapers.
    Guarantees consistent async generator streaming and fault containment.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        request_delay: float = 1.0,
        timeout: float = 15.0,
        max_retries: int = 3,
        proxy_url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.name = name
        self.base_url = base_url
        self.request_delay = request_delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.proxy_url = proxy_url

        self.default_headers: Dict[str, str] = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "uk,ru;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Connection": "keep-alive",
        }
        if headers:
            self.default_headers.update(headers)

        self._injected_client = client
        self._client: Optional[httpx.AsyncClient] = client
        self.stats = ParserRunStats(source=name, source_name=name)

    def rotate_headers(self) -> Dict[str, str]:
        """Rotate User-Agent to avoid bot fingerprinting."""
        headers = dict(self.default_headers)
        headers["User-Agent"] = random.choice(USER_AGENTS)
        return headers

    async def get_client(self) -> httpx.AsyncClient:
        """Lazy initialization of httpx.AsyncClient with keep-alive pooling."""
        if self._injected_client is not None:
            return self._injected_client

        if self._client is None or self._client.is_closed:
            transport = (
                httpx.AsyncHTTPTransport(retries=self.max_retries, verify=True, proxy=self.proxy_url)
                if self.proxy_url
                else httpx.AsyncHTTPTransport(retries=self.max_retries, verify=True)
            )

            self._client = httpx.AsyncClient(
                headers=self.default_headers,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                transport=transport,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Gracefully release network sockets and client sessions."""
        if self._client is not None and not self._client.is_closed:
            # Only close if it wasn't externally injected or if injected should be closed
            if self._client is not self._injected_client or not self._injected_client.is_closed:
                await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> BaseParser:
        await self.get_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def fetch_response_with_retry(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        method: str = "GET",
        json_body: Optional[Any] = None,
    ) -> Optional[httpx.Response]:
        """
        Execute HTTP request with exponential backoff and jitter.
        Returns the raw httpx.Response object or None if failed.
        """
        client = await self.get_client()
        req_headers = self.rotate_headers()
        if headers:
            req_headers.update(headers)

        for attempt in range(1, self.max_retries + 1):
            try:
                if self.request_delay > 0:
                    await asyncio.sleep(self.request_delay)

                if method.upper() == "POST":
                    response = await client.post(url, params=params, json=json_body, headers=req_headers)
                else:
                    response = await client.get(url, params=params, headers=req_headers)

                # Success
                if response.status_code == 200:
                    return response

                # Rate limiting
                if response.status_code == 429:
                    retry_after_hdr = response.headers.get("Retry-After")
                    try:
                        retry_after = int(retry_after_hdr) if retry_after_hdr else (2 ** attempt) + random.uniform(1.0, 3.0)
                    except ValueError:
                        retry_after = (2 ** attempt) + random.uniform(1.0, 3.0)

                    logger.warning(
                        "[%s] 429 Rate limited on %s. Sleeping %.1fs (attempt %d/%d)",
                        self.name, url, retry_after, attempt, self.max_retries
                    )
                    await asyncio.sleep(retry_after)
                    continue

                # Non-retryable client errors
                if response.status_code in (401, 403, 404, 410):
                    logger.warning(
                        "[%s] HTTP %d for %s (non-retryable client response)",
                        self.name, response.status_code, url
                    )
                    return response

                # Server errors or unexpected status
                logger.warning(
                    "[%s] HTTP %d for %s (attempt %d/%d)",
                    self.name, response.status_code, url, attempt, self.max_retries
                )
                backoff = (2 ** attempt) + random.uniform(0.1, 0.5)
                await asyncio.sleep(backoff)

            except (httpx.RequestError, httpx.TimeoutException) as exc:
                backoff = (2 ** attempt) + random.uniform(0.2, 0.8)
                logger.warning(
                    "[%s] Network error %s on %s. Backoff %.1fs: %s",
                    self.name, type(exc).__name__, url, backoff, exc
                )
                await asyncio.sleep(backoff)
            except Exception as e:
                logger.error("[%s] Unexpected error during request to %s: %s", self.name, url, e, exc_info=True)
                break

        self.stats.record_error()
        return None

    async def fetch_html_with_retry(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        encoding_override: Optional[str] = None,
    ) -> Optional[str]:
        """
        Fetch HTML text with retries and automatic / optional charset decoding.
        """
        response = await self.fetch_response_with_retry(url, params=params, headers=headers)
        if response is None or response.status_code != 200:
            return None

        if encoding_override:
            try:
                return response.content.decode(encoding_override, errors="replace")
            except Exception as enc_err:
                logger.warning("[%s] Failed to decode response using %s: %s", self.name, encoding_override, enc_err)

        # Let httpx auto-detect or fall back to UTF-8
        try:
            return response.text
        except Exception:
            return response.content.decode("utf-8", errors="replace")

    @abc.abstractmethod
    async def fetch_new_listings(self) -> AsyncGenerator[RawListingPayload, None]:
        """
        Abstract generator yielding RawListingPayload objects.
        Must catch internal item parsing errors so the generator continues streaming.
        """
        raise NotImplementedError
