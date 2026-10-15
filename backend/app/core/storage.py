"""Files in Supabase Storage, read and written with the service key."""

import httpx

from app.core.config import get_settings


def _object_url(path: str) -> str:
    settings = get_settings()
    return f"{settings.supabase_url}/storage/v1/object/{settings.storage_bucket}/{path}"


def _headers() -> dict[str, str]:
    key = get_settings().supabase_service_role_key
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def upload(path: str, content: bytes, content_type: str) -> None:
    response = httpx.post(
        _object_url(path),
        content=content,
        headers={**_headers(), "Content-Type": content_type, "x-upsert": "false"},
        timeout=60,
    )
    response.raise_for_status()


def download(path: str) -> bytes:
    response = httpx.get(_object_url(path), headers=_headers(), timeout=60)
    response.raise_for_status()
    return response.content
