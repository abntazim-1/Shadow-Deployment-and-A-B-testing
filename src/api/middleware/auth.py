from fastapi import Security
from fastapi.security import APIKeyHeader
from src.core.config import settings
from src.core.exceptions import UnauthorizedException

api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)

async def verify_admin_api_key(api_key_header: str = Security(api_key_header)):
    """
    Dependency to verify the admin API key against the configured ADMIN_API_KEY.
    """
    if not api_key_header:
        raise UnauthorizedException(detail="Missing X-Admin-Key header")
    if api_key_header != settings.admin_api_key:
        raise UnauthorizedException(detail="Invalid API Key")
    return api_key_header
