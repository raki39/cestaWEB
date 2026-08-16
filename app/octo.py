"""
Cliente HTTP do OCTO — a única porta entre os dois projetos.

Todo tráfego para o OCTO passa por aqui. É de propósito: com um módulo só, a
resposta para "o que este app manda para o OCTO?" é uma leitura de arquivo, não
uma busca no repositório.

O contrato dos endpoints está em `CONTRATO.md`. Este módulo é a implementação do
lado cliente; o teste golden (`tests/test_contrato.py`) prende o payload àquele
documento.

Tradução de erro
----------------
Nada de `httpx.HTTPStatusError` vazando para a tela. Toda falha vira `ErroOcto`
com uma mensagem que um comprador de supermercado consegue ler, e o detalhe
técnico vai para o log. Um usuário que vê "500 Internal Server Error" liga para
o suporte; um que vê "sua sessão expirou, volte ao OCTO e clique de novo"
resolve sozinho.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from .settings import settings

logger = logging.getLogger(__name__)

# Versão do payload de `POST /cestas`. Tem de bater com SCHEMA_VERSIONS_ACEITAS no
# OCTO. Subir isto sem subir lá derruba o teste golden dos dois lados — que é
# exatamente o ponto.
SCHEMA_VERSION = 1


class ErroOcto(Exception):
    """Falha na conversa com o OCTO, com mensagem pronta para a tela."""

    def __init__(self, mensagem: str, *, status: Optional[int] = None, tecnico: str = ""):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status
        self.tecnico = tecnico


class SessaoInvalida(ErroOcto):
    """O ticket ou o grant não vale mais. A tela oferece voltar ao OCTO."""


def _headers() -> Dict[str, str]:
    return {
        "X-Service-Token": settings.OCTO_SERVICE_TOKEN,
        "Content-Type": "application/json",
        # Ajuda a achar as chamadas deste app no log do OCTO sem grep por IP.
        "User-Agent": "cestaWEB/1.0",
    }


async def _post(caminho: str, corpo: Dict[str, Any]) -> Dict[str, Any]:
    """POST no OCTO com o service token, devolvendo o JSON. Levanta `ErroOcto`."""
    if not settings.configurado:
        raise ErroOcto(
            "Este ambiente ainda não está configurado. Avise o time do OCTO.",
            tecnico=f"faltando: {', '.join(settings.faltando())}",
        )

    url = f"{settings.OCTO_API_URL}{caminho}"
    try:
        async with httpx.AsyncClient(timeout=settings.OCTO_TIMEOUT) as cli:
            r = await cli.post(url, json=corpo, headers=_headers())
    except httpx.TimeoutException as e:
        logger.warning("[OCTO] timeout em %s: %s", caminho, e)
        raise ErroOcto(
            "O OCTO demorou demais para responder. Tente de novo em alguns instantes."
        ) from e
    except httpx.HTTPError as e:
        logger.warning("[OCTO] falha de rede em %s: %s", caminho, e)
        raise ErroOcto(
            "Não consegui falar com o OCTO agora. Tente de novo em alguns instantes."
        ) from e

    if r.status_code in (401, 403):
        # 401 aqui é ambíguo por desenho: pode ser o service token (problema
        # nosso, de configuração) ou o ticket/grant (sessão do usuário). O corpo
        # diferencia; a tela não precisa saber qual.
        detalhe = _detalhe(r)
        logger.warning("[OCTO] %s recusado (%s): %s", caminho, r.status_code, detalhe)
        raise SessaoInvalida(
            "Sua sessão expirou ou o link já foi usado. Volte ao OCTO e clique no "
            "botão de cestas outra vez.",
            status=r.status_code, tecnico=detalhe,
        )

    if r.status_code == 400:
        detalhe = _detalhe(r)
        logger.error("[OCTO] %s recusou o payload: %s", caminho, detalhe)
        raise ErroOcto(
            "O OCTO recusou os dados enviados. Isso é um problema nosso, não seu — "
            "avise o time.",
            status=400, tecnico=detalhe,
        )

    if r.status_code >= 400:
        detalhe = _detalhe(r)
        logger.error("[OCTO] %s devolveu %s: %s", caminho, r.status_code, detalhe)
        raise ErroOcto(
            "O OCTO respondeu com erro. Tente de novo; se insistir, avise o time.",
            status=r.status_code, tecnico=detalhe,
        )

    try:
        return r.json()
    except ValueError as e:
        logger.error("[OCTO] %s devolveu corpo não-JSON: %r", caminho, r.text[:300])
        raise ErroOcto("Resposta inesperada do OCTO.", tecnico=r.text[:300]) from e


def _detalhe(r: httpx.Response) -> str:
    """Extrai o `detail` do FastAPI, ou o texto cru. Só para log."""
    try:
        corpo = r.json()
    except ValueError:
        return r.text[:300]
    if isinstance(corpo, dict):
        return str(corpo.get("detail") or corpo)[:300]
    return str(corpo)[:300]


# ── Endpoints ───────────────────────────────────────────────────────────────────
async def validar_ticket(ticket: str) -> Dict[str, Any]:
    """Ticket da URL → identidade + grant de sessão.

    O ticket é QUEIMADO no OCTO nesta chamada (uso único). Por isso este app não
    guarda o ticket em lugar nenhum: se guardasse, teria um valor inútil que
    parece credencial — e alguém acabaria tentando reusar.
    """
    dados = await _post("/cestas/handoff/validate", {"ticket": ticket})
    if not dados.get("grant"):
        raise SessaoInvalida("O link de acesso não é válido. Volte ao OCTO e tente de novo.")
    logger.info("[OCTO] ticket validado | usuario=%s | empresa=%r",
                dados.get("usuario_id"), dados.get("empresa_nome"))
    return dados


async def enviar_cesta(
    *,
    grant: str,
    nome: str,
    origem: str,
    itens: List[Dict[str, Any]],
    idempotency_key: str,
    bandeira: Optional[str] = None,
) -> Dict[str, Any]:
    """Grava a cesta no OCTO. Devolve `{cesta_id, total_itens, duplicada, casamento}`.

    O `empresa_id`/dono NÃO vai no corpo: quem decide é o grant, do lado do OCTO.
    Mandar daqui seria deixar o app de fora escolher em nome de quem grava.
    """
    payload = monta_payload(
        grant=grant, nome=nome, origem=origem, itens=itens,
        idempotency_key=idempotency_key, bandeira=bandeira,
    )
    dados = await _post("/cestas", payload)
    logger.info("[OCTO] cesta gravada | id=%s | itens=%s | duplicada=%s",
                dados.get("cesta_id"), dados.get("total_itens"), dados.get("duplicada"))
    return dados


def monta_payload(
    *,
    grant: str,
    nome: str,
    origem: str,
    itens: List[Dict[str, Any]],
    idempotency_key: str,
    bandeira: Optional[str] = None,
) -> Dict[str, Any]:
    """Monta o corpo de `POST /cestas`.

    Função separada do envio porque é ela que o teste golden compara com o
    exemplo do `CONTRATO.md` — sem precisar de rede nem de OCTO no ar.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "grant": grant,
        "idempotency_key": idempotency_key,
        "nome": nome,
        "bandeira": bandeira,
        "origem": origem,
        "itens": itens,
    }
