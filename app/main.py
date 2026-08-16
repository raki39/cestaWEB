"""
`cestaWEB` — app de upload de cestas, fora do OCTO.

O que ele é
-----------
Três telas: entrar (pelo ticket do OCTO), enviar a planilha, ver o resultado.
Nenhuma delas pede senha: o usuário já está autenticado no OCTO e chega aqui com
um ticket de uso único que este app troca por um `grant` de sessão.

Por que ele existe separado
---------------------------
Abrir e normalizar planilha é trabalho de CPU, e não pode acontecer no container
que atende o chat do OCTO. Aqui acontece longe, num deploy que pode reiniciar,
travar ou lotar de memória sem que ninguém no chat perceba.

A fronteira, em uma frase
-------------------------
Este app **nunca** vê a base de mercado e **nunca** tem credencial de banco. Ele
conhece uma URL HTTP e um token. Tudo que sabe sobre gente vem de perguntar ao
OCTO — ver `app/octo.py`, que é a única porta entre os dois lados.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .modelo import NOME_ARQUIVO, gerar_modelo
from .octo import ErroOcto, SessaoInvalida, enviar_cesta, validar_ticket
from .planilha import MAX_ITENS, PlanilhaInvalida, ler_planilha
from .settings import settings

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("cestaweb")

AQUI = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(AQUI, "templates"))

app = FastAPI(
    title="cestaWEB",
    description="Upload de cestas personalizadas para a Simulação de Tabloide do OCTO.",
    version="1.0.0",
    # Sem /docs: este app é para o cliente final, não para integração. A superfície
    # pública menor também dá menos pista sobre os endpoints do OCTO por trás.
    docs_url=None, redoc_url=None, openapi_url=None,
)

# A sessão guarda o grant. `https_only` fica ligado fora de dev — o Railway
# termina TLS, então o cookie nunca precisa viajar em claro.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET or "sem-segredo-app-recusa-acesso",
    session_cookie=settings.SESSION_COOKIE,
    max_age=2 * 60 * 60,                      # casa com a validade do grant
    same_site="lax",                          # o usuário chega por link de outro site
    https_only=os.getenv("ENV", "prod") != "local",
)

# Cesta com mais itens que isto não é recusada — o parser corta e avisa. O número
# aparece na tela para o cliente saber antes de montar a planilha.
LIMITE_EXIBIDO = f"{MAX_ITENS:,}".replace(",", ".")


# ── Sessão ──────────────────────────────────────────────────────────────────────
def _sessao(request: Request) -> Optional[Dict[str, Any]]:
    """Dados do usuário logado, ou None. Só confia na sessão se ela tiver grant."""
    dados = request.session.get("octo")
    if isinstance(dados, dict) and dados.get("grant"):
        return dados
    return None


def _pagina(request: Request, template: str, **ctx) -> HTMLResponse:
    base = {
        "request": request,
        "sessao": _sessao(request),
        "stack": settings.STACK_LABEL,
        "octo_front": settings.OCTO_FRONT_URL,
        "limite_itens": LIMITE_EXIBIDO,
    }
    base.update(ctx)
    return templates.TemplateResponse(template, base)


def _erro(request: Request, titulo: str, mensagem: str, *, status: int = 400,
          oferecer_voltar: bool = False) -> HTMLResponse:
    r = _pagina(request, "erro.html", titulo=titulo, mensagem=mensagem,
                oferecer_voltar=oferecer_voltar)
    r.status_code = status
    return r


# ── Saúde ───────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    """Denuncia configuração faltando em vez de esperar o cliente descobrir."""
    return {
        "status": "ok" if settings.configurado else "sem_configuracao",
        "stack": settings.STACK_LABEL,
        "octo_api": bool(settings.OCTO_API_URL),
        "faltando": settings.faltando(),
    }


# ── Entrada pelo ticket ─────────────────────────────────────────────────────────
@app.get("/entrar")
async def entrar(request: Request, ticket: str = ""):
    """Recebe o ticket do OCTO, troca por grant e redireciona.

    O redirect no fim não é enfeite: ele tira o ticket da barra de endereço. Sem
    isso o ticket fica no histórico do navegador e no `Referer` de qualquer
    requisição seguinte — e ticket em log de proxy é credencial em log de proxy,
    ainda que de uso único.
    """
    if not ticket.strip():
        return _erro(request, "Link incompleto",
                     "Este endereço precisa ser aberto pelo botão de cestas dentro do OCTO.",
                     status=400, oferecer_voltar=True)

    try:
        dados = await validar_ticket(ticket.strip())
    except SessaoInvalida as e:
        return _erro(request, "Link expirado ou já usado", e.mensagem,
                     status=401, oferecer_voltar=True)
    except ErroOcto as e:
        logger.error("[ENTRAR] %s | tecnico=%s", e.mensagem, e.tecnico)
        return _erro(request, "Não consegui validar seu acesso", e.mensagem, status=502)

    request.session["octo"] = {
        "grant": dados["grant"],
        "usuario_id": dados.get("usuario_id"),
        "usuario_nome": dados.get("usuario_nome") or "",
        "usuario_email": dados.get("usuario_email") or "",
        "empresa_nome": dados.get("empresa_nome") or "",
        "grant_expira_em": dados.get("grant_expira_em") or "",
    }
    return RedirectResponse("/", status_code=303)


@app.get("/sair")
def sair(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ── Upload ──────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def inicio(request: Request):
    if not settings.configurado:
        return _erro(request, "Ambiente não configurado",
                     "Este ambiente ainda não foi configurado. Avise o time do OCTO.",
                     status=503)
    if _sessao(request) is None:
        return _pagina(request, "entrar.html")
    return _pagina(request, "upload.html")


@app.post("/enviar", response_class=HTMLResponse)
async def enviar(
    request: Request,
    arquivo: UploadFile = File(...),
    nome: str = Form(""),
    bandeira: str = Form(""),
):
    sessao = _sessao(request)
    if sessao is None:
        return _erro(request, "Sessão expirada",
                     "Volte ao OCTO e clique no botão de cestas outra vez.",
                     status=401, oferecer_voltar=True)

    nome_arquivo = os.path.basename(arquivo.filename or "planilha.xlsx")
    if not nome_arquivo.lower().endswith((".xlsx", ".xlsm")):
        return _pagina(request, "upload.html", erro=(
            "Envie um arquivo .xlsx. Se a sua planilha é .xls ou .csv, abra no Excel "
            "e use 'Salvar como → Pasta de Trabalho do Excel (.xlsx)'."
        ))

    # Leitura com teto: em pedaços, para um arquivo enorme não virar memória antes
    # de ser recusado. `content-length` não serve — o cliente escolhe o que declara.
    pedacos, total = [], 0
    while True:
        pedaco = await arquivo.read(256 * 1024)
        if not pedaco:
            break
        total += len(pedaco)
        if total > settings.MAX_UPLOAD_BYTES:
            mb = settings.MAX_UPLOAD_BYTES // (1024 * 1024)
            logger.info("[ENVIAR] recusado por tamanho | usuario=%s | >%d MB",
                        sessao.get("usuario_id"), mb)
            return _pagina(request, "upload.html", erro=(
                f"O arquivo passa de {mb} MB. Remova abas e formatação extra, ou divida a planilha."
            ))
        pedacos.append(pedaco)
    conteudo = b"".join(pedacos)

    if not conteudo:
        return _pagina(request, "upload.html", erro="O arquivo chegou vazio. Tente enviar de novo.")

    try:
        leitura = ler_planilha(conteudo, nome_arquivo)
    except PlanilhaInvalida as e:
        logger.info("[ENVIAR] planilha recusada | usuario=%s | %s", sessao.get("usuario_id"), e)
        return _pagina(request, "upload.html", erro=str(e))

    # Idempotência: o conteúdo do arquivo é a identidade do envio. Duplo clique e
    # retry caem na mesma chave, e o OCTO devolve a cesta que já existe em vez de
    # criar uma gêmea no seletor.
    idem = "sha256:" + hashlib.sha256(conteudo).hexdigest()

    try:
        resposta = await enviar_cesta(
            grant=sessao["grant"],
            nome=(nome.strip() or os.path.splitext(nome_arquivo)[0])[:200],
            bandeira=(bandeira.strip() or None),
            origem=nome_arquivo[:255],
            itens=leitura.itens,
            idempotency_key=idem,
        )
    except SessaoInvalida as e:
        request.session.clear()
        return _erro(request, "Sessão expirada", e.mensagem, status=401, oferecer_voltar=True)
    except ErroOcto as e:
        logger.error("[ENVIAR] OCTO recusou | usuario=%s | %s | tecnico=%s",
                     sessao.get("usuario_id"), e.mensagem, e.tecnico)
        # A planilha foi lida com sucesso — mostramos o que foi lido junto com o
        # erro, para o cliente saber que o problema é no envio e não na planilha.
        return _pagina(request, "upload.html", erro=e.mensagem,
                       resumo=leitura.resumo, avisos=leitura.avisos)

    logger.info("[ENVIAR] ok | usuario=%s | cesta=%s | itens=%s | duplicada=%s",
                sessao.get("usuario_id"), resposta.get("cesta_id"),
                resposta.get("total_itens"), resposta.get("duplicada"))

    return _pagina(request, "resultado.html", resposta=resposta,
                   resumo=leitura.resumo, avisos=leitura.avisos)


# ── Modelo ──────────────────────────────────────────────────────────────────────
@app.get("/modelo.xlsx")
def modelo():
    """O `.xlsx` que o cliente preenche, gerado a partir do próprio parser."""
    return Response(
        content=gerar_modelo(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{NOME_ARQUIVO}"'},
    )
