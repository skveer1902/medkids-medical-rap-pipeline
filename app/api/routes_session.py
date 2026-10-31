"""Small helper endpoints used by the web UI: current principal and sample topics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_principal
from app.services.security import PERMISSIONS, Principal

router = APIRouter(prefix="/api", tags=["session"])

SAMPLE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "sample_topics"


@router.get("/me", summary="Who the current credentials belong to and what they may do")
def me(request: Request, principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    settings = request.app.state.settings
    return {
        "name": principal.name,
        "role": principal.role,
        "permissions": sorted(p for p, roles in PERMISSIONS.items() if principal.role in roles),
        "required_approval_roles": list(settings.required_approval_roles),
        "llm_provider": settings.llm_provider,
        "app_env": settings.app_env,
    }


@router.get("/samples", summary="Demonstration topic payloads (Heart, Brain) for pre-filling the topic form")
def samples(_: Principal = Depends(get_principal)) -> list[dict[str, Any]]:
    out = []
    for path in sorted(SAMPLE_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        out.append({k: v for k, v in raw.items() if not k.startswith("_")})
    return out
