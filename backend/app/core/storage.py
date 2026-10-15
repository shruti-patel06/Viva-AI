"""Files in Supabase Storage, read and written with the service key."""

import httpx

from app.core.config import get_settings


def _object_url(bucket: str, path: str) -> str:
    return f"{get_settings().supabase_url}/storage/v1/object/{bucket}/{path}"


def _headers() -> dict[str, str]:
    key = get_settings().supabase_service_role_key
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def upload(bucket: str, path: str, content: bytes, content_type: str, overwrite: bool = False) -> None:
    response = httpx.post(
        _object_url(bucket, path),
        content=content,
        headers={**_headers(), "Content-Type": content_type, "x-upsert": "true" if overwrite else "false"},
        timeout=60,
    )
    response.raise_for_status()


def download(bucket: str, path: str) -> bytes:
    response = httpx.get(_object_url(bucket, path), headers=_headers(), timeout=60)
    response.raise_for_status()
    return response.content


def remove(bucket: str, path: str) -> None:
    response = httpx.delete(_object_url(bucket, path), headers=_headers(), timeout=60)
    response.raise_for_status()


def signed_url(bucket: str, path: str, expires_in_seconds: int = 600) -> str:
    """A link anyone can open until it expires. The bucket itself stays private."""
    settings = get_settings()
    response = httpx.post(
        f"{settings.supabase_url}/storage/v1/object/sign/{bucket}/{path}",
        json={"expiresIn": expires_in_seconds},
        headers=_headers(),
        timeout=30,
    )
    response.raise_for_status()
    return f"{settings.supabase_url}/storage/v1{response.json()['signedURL']}"
