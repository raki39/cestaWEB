"""
Testes do leitor de planilha.

Cada teste monta um `.xlsx` de verdade em memória (openpyxl) e passa pelo parser
real — não há mock do formato. É o único jeito de provar que a armadilha do EAN
em notação científica está resolvida: ela nasce de como o Excel guarda o número,
então precisa de um arquivo Excel para aparecer.

Rodar de dentro de `cestaWEB/`:
    python -m pytest tests/test_planilha.py -q
    python tests/test_planilha.py          # sem pytest instalado
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl

from app.planilha import (
    MAX_ITENS,
    PlanilhaInvalida,
    _limpa_ean,
    _limpa_preco,
    _norm_cabecalho,
    ler_planilha,
)


def xlsx(linhas) -> bytes:
    """Lista de linhas → bytes de um .xlsx real."""
    wb = openpyxl.Workbook()
    aba = wb.active
    for linha in linhas:
        aba.append(list(linha))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


CABECALHO = ("descricao", "ean", "preco", "marca", "categoria")


# ── o caminho feliz ─────────────────────────────────────────────────────────────
def test_planilha_do_modelo_e_lida_inteira():
    b = xlsx([
        CABECALHO,
        ("OLEO DE SOJA SOYA 900ML", "7891107101012", 7.49, "SOYA", "MERCEARIA"),
        ("CAFE TORRADO E MOIDO 500G", None, "15,90", None, None),
    ])
    r = ler_planilha(b, "modelo.xlsx")

    assert r.resumo["itens"] == 2
    assert [i["ordem"] for i in r.itens] == [1, 2]
    assert r.itens[0] == {
        "ordem": 1,
        "descricao_cliente": "OLEO DE SOJA SOYA 900ML",
        "ean": "7891107101012",
        "preco": 7.49,
        "marca": "SOYA",
        "categoria": "MERCEARIA",
    }
    # Preço no formato brasileiro sobreviveu, e os opcionais vazios são None (não "").
    assert r.itens[1]["preco"] == 15.90
    assert r.itens[1]["marca"] is None and r.itens[1]["ean"] is None


def test_item_sai_com_exatamente_os_campos_do_contrato():
    """Whitelist: nenhum campo extra escapa daqui para o payload."""
    b = xlsx([CABECALHO, ("ARROZ", "7891234567895", 10.0, "TIO", "MERCEARIA")])
    esperado = {"ordem", "descricao_cliente", "ean", "preco", "marca", "categoria"}
    assert set(ler_planilha(b).itens[0]) == esperado


# ── EAN: a armadilha do Excel ───────────────────────────────────────────────────
def test_ean_que_o_excel_guardou_como_numero_nao_vira_notacao_cientifica():
    """O caso real: EAN digitado sem formatar a coluna vira float.

    `str(7891107101012.0)` é '7891107101012.0' em float grande, mas o mesmo dado
    passando por planilha pode chegar como 7.891107101012e+12. O teste passa o
    float direto — que é o que o openpyxl entrega — e exige dígitos limpos.
    """
    b = xlsx([CABECALHO, ("OLEO", 7891107101012, 7.49, None, None)])
    assert ler_planilha(b).itens[0]["ean"] == "7891107101012"

    # float não perde dígito: float64 guarda 13 dígitos exatos e `str()` devolve o
    # repr curto que faz round-trip, então a reconstrução é fiel.
    assert _limpa_ean(7.891107101012e12) == ("7891107101012", None)
    assert "e+" not in str(_limpa_ean(7.891107101012e12)[0]).lower()


def test_ean_salvo_em_notacao_cientifica_e_recusado_em_vez_de_inventar_digitos():
    """O caso perigoso: a célula foi SALVA como '7,89111E+12'.

    Os dígitos do fim não existem mais no arquivo. Reconstruir daria
    '7891110000000' — treze dígitos, formato válido e errado (o EAN real era
    7891107101012). Um EAN plausível-mas-errado é pior que um EAN vazio, porque
    ninguém desconfia dele. Então a regra é recusar e dizer o motivo.
    """
    ean, motivo = _limpa_ean("7,89111E+12")
    assert ean is None, "não podemos fabricar os zeros que o Excel comeu"
    assert "TEXTO" in motivo

    # Mantissa completa (13 significativos) NÃO perde nada e continua aceita.
    assert _limpa_ean("7.891107101012E+12") == ("7891107101012", None)


def test_ean_com_tamanho_invalido_vira_none_com_aviso_e_o_item_continua():
    b = xlsx([CABECALHO, ("OLEO", "123", 7.49, None, None)])
    r = ler_planilha(b)
    assert r.itens[0]["ean"] is None
    assert len(r.itens) == 1, "o produto tem de entrar; só o EAN é descartado"
    assert any("código de barras descartado" in a for a in r.avisos)
    assert any("3 dígitos" in a for a in r.avisos)


def test_ean_aceita_os_quatro_tamanhos_e_recusa_o_resto():
    for digitos in (8, 12, 13, 14):
        val = "9" * digitos
        assert _limpa_ean(val) == (val, None), f"{digitos} dígitos deveria passar"
    for digitos in (7, 9, 11, 15):
        ean, motivo = _limpa_ean("9" * digitos)
        assert ean is None and motivo, f"{digitos} dígitos deveria ser recusado"


def test_ean_com_separadores_e_limpo():
    assert _limpa_ean("789-1107 101.012")[0] == "7891107101012"


# ── Preço ───────────────────────────────────────────────────────────────────────
def test_preco_aceita_os_formatos_que_aparecem_de_verdade():
    casos = {
        7.49: 7.49, "7,49": 7.49, "R$ 7,49": 7.49, "r$7.49": 7.49,
        "1.234,56": 1234.56, "1,234.56": 1234.56, "15,90": 15.90, 10: 10.0,
    }
    for entrada, esperado in casos.items():
        valor, motivo = _limpa_preco(entrada)
        assert valor == esperado, f"{entrada!r} → {valor} (esperado {esperado}); motivo={motivo}"


def test_preco_invalido_derruba_a_linha_com_o_numero_da_linha_do_excel():
    b = xlsx([
        CABECALHO,
        ("ARROZ", None, 0, None, None),          # linha 2: zero
        ("FEIJAO", None, "grátis", None, None),  # linha 3: texto
        ("CAFE", None, -5, None, None),          # linha 4: negativo
        ("OLEO", None, 7.49, None, None),        # linha 5: única boa
    ])
    r = ler_planilha(b)
    assert len(r.itens) == 1 and r.itens[0]["descricao_cliente"] == "OLEO"
    assert r.resumo["linhas_descartadas"] == 3
    for n in ("2", "3", "4"):
        assert any(f"Linha {n}" in a for a in r.avisos), f"faltou avisar da linha {n}"


# ── Cabeçalho ───────────────────────────────────────────────────────────────────
def test_cabecalho_e_casado_sem_acento_sem_caixa_e_com_alias():
    b = xlsx([
        ("Descrição do Produto", "Código de Barras", "PREÇO PROMOCIONAL", "Fabricante", "Departamento"),
        ("ARROZ", "7891234567895", "10,00", "TIO", "MERCEARIA"),
    ])
    r = ler_planilha(b)
    assert r.itens[0]["marca"] == "TIO"
    assert r.itens[0]["categoria"] == "MERCEARIA"
    assert r.itens[0]["ean"] == "7891234567895"


def test_cabecalho_encontrado_abaixo_de_titulo_e_linha_em_branco():
    b = xlsx([
        ("TABLOIDE SETEMBRO — CLIENTE EXEMPLO",),
        (),
        CABECALHO,
        ("ARROZ", None, 10.0, None, None),
    ])
    r = ler_planilha(b)
    assert r.resumo["linha_cabecalho"] == 3
    assert len(r.itens) == 1


def test_sem_coluna_obrigatoria_a_planilha_e_recusada_com_o_nome_da_coluna():
    b = xlsx([("descricao", "marca"), ("ARROZ", "TIO")])
    try:
        ler_planilha(b)
        assert False, "deveria ter recusado a planilha sem 'preco'"
    except PlanilhaInvalida as e:
        assert "preco" in str(e)


def test_coluna_desconhecida_e_ignorada_com_aviso():
    b = xlsx([
        ("descricao", "preco", "estoque interno"),
        ("ARROZ", 10.0, 42),
    ])
    r = ler_planilha(b)
    assert len(r.itens) == 1
    assert any("estoque interno" in a for a in r.avisos)


def test_norm_cabecalho_tira_pontuacao_de_borda():
    assert _norm_cabecalho("Preço (R$):") == "preco (r$)"
    assert _norm_cabecalho("  DESCRIÇÃO_DO_PRODUTO ") == "descricao do produto"


# ── Linhas ruins e limites ──────────────────────────────────────────────────────
def test_linha_em_branco_no_meio_nao_conta_como_descartada():
    b = xlsx([CABECALHO, ("ARROZ", None, 10.0, None, None), (), (None, None, None),
              ("FEIJAO", None, 8.0, None, None)])
    r = ler_planilha(b)
    assert len(r.itens) == 2
    assert r.resumo["linhas_descartadas"] == 0


def test_duplicata_mantem_a_primeira_e_avisa_qual_linha():
    b = xlsx([CABECALHO,
              ("ARROZ TIO 5KG", "7891234567895", 10.0, None, None),
              ("arroz  tio  5kg", "7891234567895", 12.0, None, None)])
    r = ler_planilha(b)
    assert len(r.itens) == 1 and r.itens[0]["preco"] == 10.0
    assert r.resumo["linhas_repetidas"] == 1
    assert any("repetida da linha 2" in a for a in r.avisos)


def test_planilha_vazia_e_sem_linha_aproveitavel_sao_recusadas():
    for linhas, motivo in [([], "aba vazia"), ([CABECALHO], "só cabeçalho")]:
        try:
            ler_planilha(xlsx(linhas))
            assert False, f"deveria recusar: {motivo}"
        except PlanilhaInvalida:
            pass


def test_arquivo_que_nao_e_xlsx_da_mensagem_de_tela_nao_stacktrace():
    try:
        ler_planilha(b"descricao;preco\nARROZ;10", "cesta.csv")
        assert False, "deveria recusar um CSV renomeado"
    except PlanilhaInvalida as e:
        assert ".xlsx" in str(e)


def test_teto_de_itens_e_recusa_explicita_nao_corte_silencioso():
    linhas = [CABECALHO] + [(f"PRODUTO {i}", None, 1.0 + i, None, None)
                            for i in range(MAX_ITENS + 25)]
    r = ler_planilha(xlsx(linhas))
    assert len(r.itens) == MAX_ITENS
    assert r.resumo["truncada"] is True
    assert any("parei" in a for a in r.avisos), "cortar sem avisar é o erro que não podemos ter"


def test_descricao_longa_e_truncada_no_limite_do_contrato():
    b = xlsx([CABECALHO, ("X" * 900, None, 10.0, None, None)])
    assert len(ler_planilha(b).itens[0]["descricao_cliente"]) == 500


def test_resumo_conta_ean_e_marca():
    b = xlsx([CABECALHO,
              ("ARROZ", "7891234567895", 10.0, "TIO", None),
              ("FEIJAO", None, 8.0, None, None)])
    r = ler_planilha(b)
    assert r.resumo["com_ean"] == 1 and r.resumo["com_marca"] == 1


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
