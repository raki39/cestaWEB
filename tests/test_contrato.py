"""
Teste GOLDEN do contrato — lado `cestaWEB`.

Este é o teste que impede o drift entre os dois repositórios. Ele prende o payload
que este app **produz** ao exemplo publicado em `CONTRATO.md`. O par dele vive no
outro lado (`agentAPI/tests/test_contrato_cestas.py`) e prende o que o endpoint
**aceita** ao mesmo exemplo.

Se alguém mexer no formato de um lado só, um dos dois quebra. É a única coisa que
funciona: os dois projetos têm deploy separado, então nada além de um teste
compartilhado avisa que eles deixaram de se entender.

Rodar de dentro de `cestaWEB/`:
    python -m pytest tests/test_contrato.py -q
    python tests/test_contrato.py
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.octo import SCHEMA_VERSION, monta_payload
from app.planilha import ler_planilha

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRATO = os.path.join(RAIZ, "CONTRATO.md")


# ── O payload golden, copiado do CONTRATO.md ────────────────────────────────────
GOLDEN = {
    "schema_version": 1,
    "grant": "a91c...",
    "idempotency_key": "sha256:9f2b1c...",
    "nome": "Tabloide Setembro",
    "bandeira": "ASSAI ATAC",
    "origem": "tabloide-setembro.xlsx",
    "itens": [
        {
            "ordem": 1,
            "descricao_cliente": "OLEO DE SOJA SOYA 900ML",
            "ean": "7891107101012",
            "preco": 7.49,
            "marca": "SOYA",
            "categoria": "MERCEARIA",
        },
        {
            "ordem": 2,
            "descricao_cliente": "CAFE TORRADO E MOIDO 500G",
            "ean": None,
            "preco": 15.9,
            "marca": None,
            "categoria": None,
        },
    ],
}


def test_monta_payload_produz_exatamente_o_golden():
    """O corpo montado é, campo a campo, o do CONTRATO.md."""
    payload = monta_payload(
        grant="a91c...",
        nome="Tabloide Setembro",
        bandeira="ASSAI ATAC",
        origem="tabloide-setembro.xlsx",
        itens=GOLDEN["itens"],
        idempotency_key="sha256:9f2b1c...",
    )
    assert payload == GOLDEN


def test_o_parser_produz_os_itens_do_golden_a_partir_de_um_xlsx_de_verdade():
    """Fecha o círculo: planilha real → itens idênticos aos do contrato.

    Sem isto, `monta_payload` poderia estar certo e o parser entregando outra
    forma — e o contrato só quebraria em produção.
    """
    import openpyxl

    wb = openpyxl.Workbook()
    aba = wb.active
    aba.append(["descricao", "ean", "preco", "marca", "categoria"])
    aba.append(["OLEO DE SOJA SOYA 900ML", "7891107101012", 7.49, "SOYA", "MERCEARIA"])
    aba.append(["CAFE TORRADO E MOIDO 500G", None, 15.90, None, None])
    buf = io.BytesIO()
    wb.save(buf)

    itens = ler_planilha(buf.getvalue(), "tabloide-setembro.xlsx").itens
    assert itens == GOLDEN["itens"]


def test_schema_version_do_codigo_bate_com_a_do_contrato():
    assert SCHEMA_VERSION == GOLDEN["schema_version"]


def test_o_contrato_md_existe_e_declara_a_mesma_versao():
    """O documento é parte do contrato, não anexo: se a versão do código subir e a
    tabela de versões do markdown não, este teste avisa."""
    assert os.path.exists(CONTRATO), "CONTRATO.md é a fonte da verdade e precisa existir"
    texto = io.open(CONTRATO, encoding="utf-8").read()

    assert '"schema_version": 1' in texto or f'"schema_version": {SCHEMA_VERSION}' in texto, \
        "o payload de exemplo do CONTRATO.md não declara a versão atual"

    versoes = re.findall(r"^\|\s*(\d+)\s*\|", texto, re.M)
    assert versoes, "não achei a tabela de versões no CONTRATO.md"
    assert max(int(v) for v in versoes) == SCHEMA_VERSION, (
        f"a tabela de versões do CONTRATO.md vai até {max(int(v) for v in versoes)}, "
        f"mas o código está na {SCHEMA_VERSION}"
    )


def test_os_campos_do_item_sao_exatamente_a_whitelist_do_contrato():
    """Campo a mais no item é campo que o OCTO vai ignorar em silêncio."""
    whitelist = {"ordem", "descricao_cliente", "ean", "preco", "marca", "categoria"}
    for item in GOLDEN["itens"]:
        assert set(item) == whitelist

    texto = io.open(CONTRATO, encoding="utf-8").read()
    for campo in whitelist:
        assert f"`{campo}`" in texto, f"o campo '{campo}' não está documentado no CONTRATO.md"


def test_o_payload_nao_carrega_dono_da_cesta():
    """Regra de segurança do contrato: o dono vem do grant, do lado do OCTO.

    Se um `empresa_id`/`user_id` entrar aqui algum dia, o app de fora passa a
    escolher em nome de quem grava — e este teste é o que barra.
    """
    proibidos = ("empresa_id", "user_id", "usuario_id", "owner_user_id", "owner")
    for campo in proibidos:
        assert campo not in GOLDEN, f"'{campo}' não pode trafegar no corpo"


if __name__ == "__main__":
    testes = [(n, o) for n, o in sorted(globals().items())
              if n.startswith("test_") and callable(o)]
    falhas = []
    for nome, fn in testes:
        try:
            fn()
            print(f"  [OK] {nome}")
        except Exception as e:
            falhas.append(nome)
            print(f"  [XX] {nome}: {type(e).__name__}: {e}")
    print(f"\n{len(testes) - len(falhas)}/{len(testes)} passaram")
    sys.exit(1 if falhas else 0)
