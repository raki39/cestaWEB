# cestaWEB

Upload de **cestas personalizadas** para a Simulação de Tabloide do OCTO.

O cliente clica num botão dentro do OCTO, cai aqui já autenticado, envia a
planilha do tabloide dele, e a cesta aparece no seletor do tema
**Simulação de Tabloide**.

---

## Por que este projeto é separado do OCTO

Abrir e normalizar planilha é trabalho de CPU. Se rodasse dentro do OCTO, rodaria
no mesmo container que atende o chat — e uma planilha grande de um cliente viraria
lentidão para todos os outros. Aqui isso acontece longe: este app pode reiniciar,
travar ou lotar de memória sem que ninguém no chat perceba.

A separação é **de repositório**, não só de pasta, e isso é de propósito. Numa
pasta dentro do `agentAPI`, alguém acabaria escrevendo `from api.models import User`
às 23h — e aí o "desacoplado" teria o banco do OCTO no `sys.path`. Repositórios
separados fazem o atalho impossível, não apenas desaconselhado.

### A fronteira, em uma lista

Este app **nunca**:

- abre o ClickHouse ou qualquer base de mercado;
- tem credencial de banco (não existe `DATABASE_URL` aqui);
- lê a tabela `users` nem descobre quais e-mails existem;
- decide de quem é a cesta (quem decide é o OCTO, pelo `grant`);
- importa qualquer coisa de `agentAPI`.

Tudo que ele sabe sobre gente vem de **perguntar ao OCTO** por HTTP. A única
porta é [`app/octo.py`](app/octo.py) — com um módulo só, "o que este app manda
para o OCTO?" é uma leitura de arquivo, não uma busca no repositório.

O contrato completo está em **[CONTRATO.md](CONTRATO.md)**, e ele não é
documentação a posteriori: os dois lados têm teste golden apontando para o
payload de exemplo de lá.

---

## Como o acesso funciona

Não há senha nem e-mail. O usuário já está logado no OCTO; o que atravessa é um
**ticket opaco de uso único** — mesmo padrão que o OCTO já usa para receber o SSO
do portal, com os papéis invertidos.

```
OCTO front ──1─► OCTO API          pede um ticket (JWT normal do usuário)
OCTO front ──2─► cestaWEB          abre /entrar?ticket=…
cestaWEB   ──3─► OCTO API          troca o ticket por um grant (queima o ticket)
cestaWEB   ──4─► OCTO API          POST /cestas com service token + grant
```

| Credencial | Prova | Vida |
|---|---|---|
| `X-Service-Token` | que a chamada vem deste app | fixa, rotacionável |
| `ticket` | que o usuário acabou de clicar no botão | 5 min, uso único |
| `grant` | em nome de quem gravar | 2 h, no cookie de sessão |

O `/entrar` termina em redirect para tirar o ticket da barra de endereço — sem
isso ele ficaria no histórico e no `Referer`, e ticket em log de proxy é
credencial em log de proxy, mesmo sendo de uso único.

---

## Rodar local

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt    # Linux/macOS: .venv/bin/python

cp .env.example .env        # e preencha OCTO_API_URL, OCTO_SERVICE_TOKEN, SESSION_SECRET
# ENV=local no .env desliga o https_only do cookie, para funcionar em http://localhost

.venv/Scripts/python -m uvicorn app.main:app --reload --port 8080
```

`GET /health` diz se falta configuração — em vez de o cliente descobrir na hora
de enviar:

```json
{ "status": "sem_configuracao", "faltando": ["OCTO_SERVICE_TOKEN"] }
```

## Testes

Sem dependência de rede e sem OCTO no ar: as duas funções que falam com ele são
substituídas por dublês.

```bash
.venv/Scripts/python -m pytest tests -q
# ou, sem pytest:
.venv/Scripts/python tests/test_planilha.py
.venv/Scripts/python tests/test_contrato.py
.venv/Scripts/python tests/test_app.py
```

| Arquivo | O que prova |
|---|---|
| `test_planilha.py` | leitura de `.xlsx` de verdade, formatos de preço, e a armadilha do EAN |
| `test_contrato.py` | **golden**: o payload produzido é o do `CONTRATO.md` |
| `test_app.py` | quem entra, quem é barrado, o que a tela diz, o que sai no payload |

---

## Modo interno (linha de comando)

O mesmo parser do app web, pela CLI — para quando for mais rápido resolver na mão
ou antes de o app estar no ar. Um parser, dois jeitos de chamar; a alternativa
seria um script separado que divergiria na primeira planilha esquisita.

```bash
python cli.py conferir tabloide.xlsx                 # lê e mostra, sem enviar nada
python cli.py conferir tabloide.xlsx --json itens.json
python cli.py enviar   tabloide.xlsx --grant <grant> --nome "Tabloide Setembro"
python cli.py modelo                                 # gera o .xlsx em branco
```

O `enviar` exige `--grant`. Não há atalho: se este script pudesse gravar sem
grant, existiria um caminho de escrita no OCTO fora da autenticação — e ele
acabaria virando o caminho normal.

---

## O modelo de planilha

`GET /modelo.xlsx` **gera** o arquivo a partir do mesmo dicionário `COLUNAS` que
o parser usa. Não é um binário commitado, e isso elimina um estado inteiro de
bug: nunca existe "o modelo pede uma coluna que o parser não conhece".

| Coluna | Obrigatória |
|---|---|
| `descricao` | sim |
| `preco` | sim |
| `ean` | não |
| `marca` | não |
| `categoria` | não |

O cabeçalho é casado sem acento e sem caixa, então "Descrição do Produto" e
"DESCRICAO" passam igual. A coluna de EAN sai formatada como **texto**: é a única
defesa contra o Excel virar `7891107101012` em `7,89111E+12`. Quando isso já
aconteceu no arquivo, os dígitos do fim se perderam — o parser detecta (a mantissa
não dá conta das casas) e **recusa o valor com aviso**, em vez de gravar um EAN
plausível e errado.

Nada é descartado em silêncio: toda linha que não vira item gera aviso com o
número da linha do Excel.

---

## Deploy no Railway

**Dois serviços com este mesmo código** — um por stack do OCTO (InfoPrice e
Interno). O que separa é só o `.env`, exatamente como `infopriceFRONT` e
`internoFRONT` já convivem hoje. Nenhuma escolha de ambiente mora em código.

1. Novo serviço a partir deste repositório (o `Dockerfile` já está pronto).
2. Cadastre as variáveis do `.env.example`. Obrigatórias: `OCTO_API_URL`,
   `OCTO_SERVICE_TOKEN`, `SESSION_SECRET`.
3. `OCTO_SERVICE_TOKEN` tem de ser **o mesmo** valor de `CESTA_SERVICE_TOKEN` no
   `.env` do OCTO daquela stack. Gere com:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
4. No OCTO daquela stack, aponte `CESTA_WEB_URL` para a URL pública deste
   serviço. É isso que faz o botão aparecer na interface.
5. Confira `GET /health`.

Não precisa de banco nem de Redis: este serviço não tem estado além do cookie de
sessão.

---

## Estrutura

```
cestaWEB/
├── CONTRATO.md          ← fonte da verdade do que trafega entre os dois lados
├── app/
│   ├── main.py           três telas + rotas
│   ├── octo.py           a ÚNICA porta para o OCTO
│   ├── planilha.py       o único lugar que abre .xlsx
│   ├── modelo.py         gera o modelo a partir do parser
│   ├── settings.py       tudo por env; um deploy por stack
│   └── templates/
├── cli.py               modo interno, mesmo parser
├── tests/
└── Dockerfile
```
