"""Sistema (admin): configuração efetiva, saúde das dependências e perfis ativos. Só leitura, sem custo."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from ....domain.auth import ADMIN, Principal
from ..security import get_service, require

router = APIRouter()


@router.get("/api/v1/system", tags=["system"],
            summary="Configuração efetiva (segredos só como definido/não definido), saúde e perfis ativos")
async def system(request: Request, _: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    return await get_service(request).system_info()
