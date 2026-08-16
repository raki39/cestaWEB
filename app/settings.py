"""
Configuração do `cestaWEB` — tudo por variável de ambiente.

Um deploy por stack
-------------------
O mesmo código roda duas vezes no Railway: um deploy apontando para a API do
OCTO **InfoPrice**, outro para a do **Interno**. Nada aqui é escolhido em código;
o que separa os dois é só o `.env` de cada serviço.

É o mesmo arranjo que `infopriceFRONT` e `internoFRONT` já usam hoje: código
igual, ambiente diferente. E é o que mantém simples a regra do contrato de que a
identidade do usuário vem sempre do OCTO que emitiu o ticket — cada deploy fala
com um OCTO só, então não há como confundir de qual base veio o usuário.

O que este projeto NÃO tem, de propósito
----------------------------------------
Credencial de banco. Não existe `DATABASE_URL` aqui. O `cestaWEB` conhece uma URL
HTTP e um token; tudo que ele sabe sobre usuários vem de perguntar ao OCTO.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(nome: str, default: int) -> int:
    try:
        return int(os.getenv(nome, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # ── OCTO ────────────────────────────────────────────────────────────────────
    # URL EXTERNA da API do OCTO (a API roda na VM, mas é publicada).
    # Ex.: https://api.octo.shoppingbrasil.com.br
    OCTO_API_URL: str = os.getenv("OCTO_API_URL", "").rstrip("/")
    # Token de serviço: prova que a chamada vem deste app. O MESMO valor tem de
    # estar em CESTA_SERVICE_TOKEN no lado do OCTO.
    OCTO_SERVICE_TOKEN: str = os.getenv("OCTO_SERVICE_TOKEN", "")
    OCTO_TIMEOUT: float = float(os.getenv("OCTO_TIMEOUT", "30"))

    # ── Sessão ──────────────────────────────────────────────────────────────────
    # Assina o cookie de sessão (onde mora o grant). Sem valor configurado o app
    # sobe, mas recusa qualquer acesso — melhor negar do que assinar com segredo
    # previsível e deixar o grant forjável.
    SESSION_SECRET: str = os.getenv("SESSION_SECRET", "")
    SESSION_COOKIE: str = os.getenv("SESSION_COOKIE", "cestaweb_sessao")

    # ── Upload ──────────────────────────────────────────────────────────────────
    MAX_UPLOAD_BYTES: int = _int_env("MAX_UPLOAD_BYTES", 5 * 1024 * 1024)   # 5 MB

    # ── Aparência ───────────────────────────────────────────────────────────────
    # Rótulo do ambiente, mostrado no rodapé. Serve para não haver dúvida sobre
    # qual OCTO este deploy atende quando os dois estiverem no ar.
    STACK_LABEL: str = os.getenv("STACK_LABEL", "OCTO")
    # Para onde o botão "voltar ao OCTO" aponta.
    OCTO_FRONT_URL: str = os.getenv("OCTO_FRONT_URL", "")

    @property
    def configurado(self) -> bool:
        """O mínimo para o app funcionar. Sem isso ele sobe mas não deixa entrar —
        o `/health` denuncia, em vez de o erro aparecer só quando o cliente tentar."""
        return bool(self.OCTO_API_URL and self.OCTO_SERVICE_TOKEN and self.SESSION_SECRET)

    def faltando(self) -> list[str]:
        return [
            nome for nome, valor in (
                ("OCTO_API_URL", self.OCTO_API_URL),
                ("OCTO_SERVICE_TOKEN", self.OCTO_SERVICE_TOKEN),
                ("SESSION_SECRET", self.SESSION_SECRET),
            ) if not valor
        ]


settings = Settings()
