from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.auth import CurrentUser, current_user

router = APIRouter(tags=["auth"])


@router.get("/me")
def me(user: Annotated[CurrentUser, Depends(current_user)]) -> dict[str, str]:
    """Who the token belongs to. Useful for checking a login works end to end."""
    return {"id": str(user.id), "role": user.role}
