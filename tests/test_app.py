"""
Testes do app web — pelo TestClient, com o OCTO substituído.

O OCTO não sobe aqui: as duas funções que falam com ele (`validar_ticket` e
`enviar_cesta`) são trocadas por dublês. O que este arquivo prova é o que é
responsabilidade DESTE app: quem entra, quem é barrado, o que a tela diz, e o que
sai no payload. O que o OCTO faz com o payload é provado no teste do outro lado.

Rodar de dentro de `cestaWEB/`:
    python -m pytest tests/test_app.py -q
    python tests/test_app.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# As settings são lidas no import do módulo, então o ambiente tem de estar pronto
# ANTES de importar o app. Sem isso o app sobe "não configurado" e todo teste vira
# 503 — um falso vermelho que já me custou tempo.
os.environ.setdefault("OCTO_API_URL", "https://octo-de-teste.invalido")
os.environ.setdefault("OCTO_SERVICE_TOKEN", "token-de-teste")
os.environ.setdefault("SESSION_SECRET", "segredo-de-teste-longo-o-suficiente")
os.environ.setdefault("OCTO_FRONT_URL", "https://front-de-teste.invalido")
os.environ.setdefault("ENV", "local")          # cookie sem https_only no TestClient

import openpyxl
from fastapi.testclient import TestClient

from app import main as appmod
from app import octo

cliente = TestClient(appmod.app)


# ── Dublês do OCTO ──────────────────────────────────────────────────────────────
IDENTIDADE = {
    "usuario_id": 42,
    "usuario_nome": "Maria",
    "usuario_email": "maria@cliente.com.br",
    "empresa_nome": "Cliente Exemplo S.A.",
    "grant": "grant-de-teste",
    "grant_expira_em": "2026-08-12T20:00:00Z",
}

enviados = []      # payloads que chegariam ao OCTO


async def _validar_ok(ticket):
    return dict(IDENTIDADE)


async def _validar_recusa(ticket):
    raise octo.SessaoInvalida("Sua sessão expirou ou o link já foi usado.")


async def _enviar_ok(**kw):
    enviados.append(kw)
    return {"cesta_id": 12, "total_itens": len(kw["itens"]),
            "duplicada": False, "casamento": {"status": "pendente"}}


async def _enviar_duplicada(**kw):
    enviados.append(kw)
    return {"cesta_id": 7, "total_itens": len(kw["itens"]),
            "duplicada": True, "casamento": {"status": "pendente"}}


async def _enviar_erro(**kw):
    raise octo.ErroOcto("O OCTO respondeu com erro.")


def usa(*, validar=None, enviar=None):
    """Troca os dublês no módulo do app (é lá que os nomes foram importados)."""
    appmod.validar_ticket = validar or _validar_ok
    appmod.enviar_cesta = enviar or _enviar_ok


def planilha(linhas=None) -> bytes:
    wb = openpyxl.Workbook()
    aba = wb.active
    aba.append(["descricao", "ean", "preco", "marca", "categoria"])
    for l in (linhas if linhas is not None else
              [["OLEO DE SOJA SOYA 900ML", "7891107101012", 7.49, "SOYA", "MERCEARIA"],
               ["CAFE TORRADO E MOIDO 500G", None, 15.90, None, None]]):
        aba.append(l)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def entra() -> TestClient:
    """Cliente já com sessão, passando pelo /entrar de verdade."""
    usa()
    c = TestClient(appmod.app)
    r = c.get("/entrar", params={"ticket": "ticket-de-teste"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c


def envia(c: TestClient, conteudo: bytes, nome_arquivo="tabloide.xlsx", **campos):
    return c.post(
        "/enviar",
        files={"arquivo": (nome_arquivo, conteudo,
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"nome": campos.get("nome", ""), "bandeira": campos.get("bandeira", "")},
    )


# ── Saúde e configuração ────────────────────────────────────────────────────────
def test_health_diz_que_esta_configurado():
    r = cliente.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["faltando"] == []


def test_o_app_nao_expoe_docs_nem_openapi():
    """Superfície pública menor: este app é para o cliente final, não para integração."""
    for caminho in ("/docs", "/redoc", "/openapi.json"):
        assert cliente.get(caminho).status_code == 404, caminho


# ── Entrada ─────────────────────────────────────────────────────────────────────
def test_sem_sessao_a_home_manda_entrar_pelo_octo():
    r = TestClient(appmod.app).get("/")
    assert r.status_code == 200
    assert "Entre pelo OCTO" in r.text
    assert "<form" not in r.text, "não pode haver formulário de upload sem sessão"


def test_entrar_sem_ticket_e_recusado():
    r = TestClient(appmod.app).get("/entrar")
    assert r.status_code == 400
    assert "botão de cestas" in r.text


def test_ticket_recusado_pelo_octo_da_401_e_oferece_voltar():
    usa(validar=_validar_recusa)
    r = TestClient(appmod.app).get("/entrar", params={"ticket": "velho"})
    assert r.status_code == 401
    assert "Ir para o OCTO" in r.text


def test_entrar_com_ticket_valido_redireciona_e_o_ticket_sai_da_url():
    """O redirect tira o ticket da barra de endereço — e do histórico e do Referer."""
    usa()
    c = TestClient(appmod.app)
    r = c.get("/entrar", params={"ticket": "bom"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"

    home = c.get("/")
    assert "Enviar uma cesta" in home.text
    assert "Maria" in home.text and "Cliente Exemplo" in home.text


def test_sair_encerra_a_sessao():
    c = entra()
    c.get("/sair", follow_redirects=False)
    assert "Entre pelo OCTO" in c.get("/").text


def test_enviar_sem_sessao_e_barrado():
    usa()
    r = envia(TestClient(appmod.app), planilha())
    assert r.status_code == 401


# ── Envio ───────────────────────────────────────────────────────────────────────
def test_envio_completo_monta_o_payload_certo_e_mostra_o_resultado():
    enviados.clear()
    c = entra()
    r = envia(c, planilha(), nome="Tabloide Setembro", bandeira="ASSAI ATAC")

    assert r.status_code == 200
    assert "Cesta enviada" in r.text and "12" in r.text

    assert len(enviados) == 1
    kw = enviados[0]
    assert kw["grant"] == "grant-de-teste"
    assert kw["nome"] == "Tabloide Setembro"
    assert kw["bandeira"] == "ASSAI ATAC"
    assert kw["origem"] == "tabloide.xlsx"
    assert kw["idempotency_key"].startswith("sha256:")
    assert len(kw["idempotency_key"]) == len("sha256:") + 64
    assert [i["ordem"] for i in kw["itens"]] == [1, 2]
    assert kw["itens"][0]["ean"] == "7891107101012"


def test_mesma_planilha_gera_a_mesma_chave_de_idempotencia():
    """É o que impede duplo clique de criar cesta gêmea no seletor."""
    enviados.clear()
    c = entra()
    conteudo = planilha()
    envia(c, conteudo)
    envia(c, conteudo)
    assert enviados[0]["idempotency_key"] == enviados[1]["idempotency_key"]


def test_planilha_diferente_gera_chave_diferente():
    enviados.clear()
    c = entra()
    envia(c, planilha())
    envia(c, planilha([["ARROZ", None, 10.0, None, None]]))
    assert enviados[0]["idempotency_key"] != enviados[1]["idempotency_key"]


def test_sem_nome_usa_o_nome_do_arquivo():
    enviados.clear()
    c = entra()
    envia(c, planilha(), nome_arquivo="tabloide-setembro.xlsx")
    assert enviados[-1]["nome"] == "tabloide-setembro"


def test_duplicada_tem_texto_proprio_em_vez_de_dizer_que_criou():
    """Nem a página nem o TÍTULO da aba podem afirmar que criou uma cesta.

    A primeira versão deste teste pegou exatamente isso: o `<title>` era fixo em
    "Cesta enviada", então a aba dizia "enviada" num envio que só reencontrou a
    cesta que já existia.
    """
    c = entra()
    usa(enviar=_enviar_duplicada)
    r = envia(c, planilha())

    assert "já estava aqui" in r.text
    assert "não criei uma cesta repetida" in r.text
    assert "Cesta enviada" not in r.text, "inclui o <title>: a aba não pode mentir"
    assert "<title>Cesta já existente" in r.text


def test_csv_renomeado_e_recusado_antes_de_ler():
    c = entra()
    r = envia(c, b"descricao;preco\nARROZ;10", nome_arquivo="cesta.csv")
    assert r.status_code == 200
    assert "Envie um arquivo .xlsx" in r.text


def test_arquivo_grande_e_recusado_pelo_teto():
    c = entra()
    original = appmod.settings.MAX_UPLOAD_BYTES
    try:
        object.__setattr__(appmod.settings, "MAX_UPLOAD_BYTES", 1024)
        r = envia(c, planilha())
        assert "passa de" in r.text and "MB" in r.text
    finally:
        object.__setattr__(appmod.settings, "MAX_UPLOAD_BYTES", original)


def test_planilha_sem_coluna_obrigatoria_volta_para_a_tela_com_o_motivo():
    c = entra()
    wb = openpyxl.Workbook(); wb.active.append(["descricao", "marca"])
    wb.active.append(["ARROZ", "TIO"])
    buf = io.BytesIO(); wb.save(buf)

    r = envia(c, buf.getvalue())
    assert r.status_code == 200
    assert "preco" in r.text
    assert "Cesta enviada" not in r.text


def test_avisos_da_planilha_aparecem_na_tela_de_resultado():
    """Nada descartado em silêncio: a linha ruim tem de aparecer para quem enviou."""
    c = entra()
    usa(enviar=_enviar_ok)
    r = envia(c, planilha([["OLEO", "7891107101012", 7.49, None, None],
                           ["SEM PRECO", None, 0, None, None]]))
    assert "Coisas que vale você saber" in r.text
    assert "Linha 3" in r.text


def test_falha_no_octo_nao_perde_o_que_foi_lido_da_planilha():
    c = entra()
    usa(enviar=_enviar_erro)
    r = envia(c, planilha())
    assert "OCTO respondeu com erro" in r.text
    assert "não é a sua planilha" in r.text


# ── Modelo ──────────────────────────────────────────────────────────────────────
def test_modelo_e_baixavel_e_passa_pelo_proprio_parser():
    from app.planilha import ler_planilha

    r = cliente.get("/modelo.xlsx")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert "modelo-cesta.xlsx" in r.headers["content-disposition"]
    # A propriedade que importa: o que entregamos ao cliente nós conseguimos ler.
    assert ler_planilha(r.content, "modelo.xlsx").resumo["itens"] == 3


if __name__ == "__main__":
    testes = [(n, o) for n, o in sorted(globals().items())
              if n.startswith("test_") and callable(o)]
    falhas = []
    for nome, fn in testes:
        try:
            usa()          # estado limpo entre testes
            fn()
            print(f"  [OK] {nome}")
        except Exception as e:
            falhas.append(nome)
            print(f"  [XX] {nome}: {type(e).__name__}: {e}")
    print(f"\n{len(testes) - len(falhas)}/{len(testes)} passaram")
    sys.exit(1 if falhas else 0)
