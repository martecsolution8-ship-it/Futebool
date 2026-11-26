from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.models.sports import ApiUsage


class ProviderHttpClient:
    def __init__(self, db: Session, provider: str, timeout: float = 30.0):
        self.db = db
        self.provider = provider
        self.timeout = timeout

    async def request(self, method: str, url: str, **kwargs):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.request(method, url, **kwargs)
                payload_size = len(response.content)
                success = 200 <= response.status_code < 300
                self.db.add(ApiUsage(
                    provider=self.provider,
                    endpoint=url,
                    http_status=response.status_code,
                    success=success,
                    response_bytes=payload_size,
                    request_at=datetime.now(timezone.utc),
                ))
                self.db.commit()
                return response
            except httpx.HTTPError as exc:
                self.db.add(ApiUsage(
                    provider=self.provider,
                    endpoint=url,
                    http_status=None,
                    success=False,
                    response_bytes=0,
                    error_message=f"{type(exc).__name__}: {exc}"[:4000],
                    request_at=datetime.now(timezone.utc),
                ))
                self.db.commit()
                raise
