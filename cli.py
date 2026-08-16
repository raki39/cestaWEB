#!/usr/bin/env python3
"""
Modo interno do `cestaWEB` — a mesma leitura de planilha, pela linha de comando.

Por que existe
--------------
Antes de o app estar no ar (e sempre que for mais rápido resolver na mão), a cesta
precisa entrar de alguma forma. A alternativa seria um script separado — e aí
existiriam DUAS implementações da mesma normalização, que divergem na primeira
planilha esquisita. Este arquivo usa exatamente o `app.planilha` do app web: um
parser, dois jeitos de chamar.

Uso
---
    # só ler e conferir, sem mandar nada
    python cli.py conferir tabloide.xlsx

    # ler e enviar ao OCTO (precisa de um grant válido)
    python cli.py enviar tabloide.xlsx --grant <grant> --nome "Tabloide Setembro"

    # gerar o modelo em branco para mandar ao cliente
    python cli.py modelo modelo-cesta.xlsx

Sobre o `--grant`
-----------------
O grant vem da mesma porta que o app web usa: alguém clica no botão no OCTO e o
ticket é trocado por grant. Não há atalho aqui de propósito — se este script
pudesse gravar cesta sem grant, existiria um caminho de escrita no OCTO que não
passa pela autenticação, e ele acabaria virando o caminho normal.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.planilha import PlanilhaInvalida, ler_planilha   # noqa: E402


def _linha(c: str = "-", n: int = 62) -> None:
    print(c * n)


def _mostra(leitura, *, amostra: int) -> None:
    r = leitura.resumo
    _linha("=")
    print(f"  arquivo            : {r['arquivo']}")
    print(f"  cabecalho na linha : {r['linha_cabecalho']}")
    print(f"  itens na cesta     : {r['itens']}")
    print(f"  com EAN            : {r['com_ean']}")
    print(f"  com marca          : {r['com_marca']}")
    print(f"  linhas de fora     : {r['linhas_descartadas']}")
    print(f"  linhas repetidas   : {r['linhas_repetidas']}")
    print(f"  EANs descartados   : {r['ean_descartados']}")
    if r["truncada"]:
        print("  ATENCAO: a planilha foi truncada no teto de itens")
    _linha("=")

    if leitura.avisos:
        print(f"\n  AVISOS ({len(leitura.avisos)}):")
        for a in leitura.avisos:
            print(f"    - {a}")

    if amostra:
        print(f"\n  PRIMEIROS {min(amostra, len(leitura.itens))} ITENS:")
        for i in leitura.itens[:amostra]:
            ean = i["ean"] or "-"
            marca = i["marca"] or "-"
            print(f"    {i['ordem']:>4}. {i['descricao_cliente'][:46]:<46} "
                  f"R$ {i['preco']:>9.2f}  ean={ean:<15} {marca}")


def _le(caminho: str):
    with io.open(caminho, "rb") as f:
        conteudo = f.read()
    return conteudo, ler_planilha(conteudo, os.path.basename(caminho))


def cmd_conferir(args) -> int:
    """Lê e mostra o que sairia — sem tocar no OCTO."""
    _, leitura = _le(args.arquivo)
    _mostra(leitura, amostra=args.amostra)
    if args.json:
        with io.open(args.json, "w", encoding="utf-8") as f:
            json.dump({"resumo": leitura.resumo, "avisos": leitura.avisos,
                       "itens": leitura.itens}, f, ensure_ascii=False, indent=2)
        print(f"\n  itens gravados em {args.json}")
    return 0


def cmd_enviar(args) -> int:
    from app.octo import ErroOcto, enviar_cesta
    from app.settings import settings

    if not settings.configurado:
        print(f"  ERRO: configuracao faltando: {', '.join(settings.faltando())}")
        print("  Defina as variaveis (ou use um .env) antes de enviar.")
        return 2

    conteudo, leitura = _le(args.arquivo)
    _mostra(leitura, amostra=args.amostra)

    nome = args.nome or os.path.splitext(os.path.basename(args.arquivo))[0]
    idem = "sha256:" + hashlib.sha256(conteudo).hexdigest()

    print(f"\n  -> enviando '{nome}' ({len(leitura.itens)} itens) para {settings.OCTO_API_URL}")
    if not args.sim:
        resposta = input("  confirmar envio? [s/N] ").strip().lower()
        if resposta not in ("s", "sim", "y"):
            print("  cancelado.")
            return 1

    try:
        r = asyncio.run(enviar_cesta(
            grant=args.grant, nome=nome[:200], bandeira=args.bandeira,
            origem=os.path.basename(args.arquivo)[:255],
            itens=leitura.itens, idempotency_key=idem,
        ))
    except ErroOcto as e:
        print(f"  ERRO: {e.mensagem}")
        if e.tecnico:
            print(f"  tecnico: {e.tecnico}")
        return 2

    _linha("=")
    print(f"  cesta_id    : {r.get('cesta_id')}")
    print(f"  total_itens : {r.get('total_itens')}")
    print(f"  duplicada   : {r.get('duplicada')}")
    print(f"  casamento   : {(r.get('casamento') or {}).get('status')}")
    _linha("=")
    return 0


def cmd_modelo(args) -> int:
    from app.modelo import gerar_modelo
    with io.open(args.saida, "wb") as f:
        f.write(gerar_modelo())
    print(f"  modelo gravado em {args.saida}")
    return 0


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"),
                        format="%(levelname)s [%(name)s] %(message)s")

    p = argparse.ArgumentParser(
        prog="cli.py",
        description="Leitura de planilha de cesta (o mesmo parser do app web).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("conferir", help="le a planilha e mostra o resultado, sem enviar")
    c.add_argument("arquivo")
    c.add_argument("--amostra", type=int, default=10, help="quantos itens listar (0 = nenhum)")
    c.add_argument("--json", help="grava os itens lidos neste arquivo .json")
    c.set_defaults(func=cmd_conferir)

    e = sub.add_parser("enviar", help="le a planilha e envia ao OCTO")
    e.add_argument("arquivo")
    e.add_argument("--grant", required=True, help="grant de sessao obtido pelo handoff do OCTO")
    e.add_argument("--nome", help="nome da cesta (default: nome do arquivo)")
    e.add_argument("--bandeira", help="bandeira do tabloide")
    e.add_argument("--amostra", type=int, default=10)
    e.add_argument("--sim", action="store_true", help="nao pergunta antes de enviar")
    e.set_defaults(func=cmd_enviar)

    m = sub.add_parser("modelo", help="gera o .xlsx modelo em branco")
    m.add_argument("saida", nargs="?", default="modelo-cesta.xlsx")
    m.set_defaults(func=cmd_modelo)

    args = p.parse_args()
    try:
        return args.func(args)
    except PlanilhaInvalida as ex:
        print(f"  PLANILHA RECUSADA: {ex}")
        return 2
    except FileNotFoundError:
        print(f"  ERRO: arquivo nao encontrado: {getattr(args, 'arquivo', '?')}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
