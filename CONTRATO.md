# Contrato de fio — `cestaWEB` ⇄ OCTO

Este arquivo é a **fonte da verdade** do que trafega entre os dois projetos. Ele
não é documentação a posteriori: os dois lados têm teste golden apontando para o
payload de exemplo daqui.

- `cestaWEB/tests/test_contrato.py` — assere que o parser **produz** este payload.
- `agentAPI/tests/test_contrato_cestas.py` — assere que o endpoint **aceita** este payload.

Se um lado mudar sem o outro, um dos dois testes quebra. É isso que impede o
drift — o texto abaixo é só a explicação.

---

## Por que existe um contrato explícito

Os dois lados vivem em repositórios separados, com deploys separados (o OCTO na
VM, o `cestaWEB` no Railway). Não há import compartilhado, não há pacote comum e
não há como um refactor de um lado quebrar o build do outro. O preço dessa
independência é que a **divergência é silenciosa**: o upload responde 201, o
cliente vê "enviado", e a cesta não aparece no seletor.

O mesmo problema já apareceu no relatório interativo, e a solução que funcionou
foi esta: versão no payload, whitelist de campos, e teste golden dos dois lados.

---

## Versionamento

Todo corpo de `POST /cestas` carrega `schema_version` (inteiro).

| Versão | Quando | O que mudou |
|--------|--------|-------------|
| 1      | 12/08/2026 | Primeira versão |

Regras:

- O OCTO **recusa** versão que não conhece, com `400` e mensagem explícita. Não
  tenta adivinhar nem aceitar "parecido" — aceitar um payload de versão
  desconhecida é como o dado errado entra sem ninguém ver.
- Campo novo **opcional** não sobe a versão.
- Campo removido, renomeado, ou que muda de tipo/significado **sobe** a versão.
- O OCTO ignora campo desconhecido (não quebra), mas registra em log — é o sinal
  de que um `cestaWEB` mais novo está falando com um OCTO mais velho.

---

## Autenticação — ticket opaco + introspecção

O usuário **já está logado no OCTO**. Ele clica num botão da interface e o
`cestaWEB` abre já autenticado — não há formulário de e-mail, não há senha, não
há envio de link.

O padrão é o mesmo que o OCTO já usa para receber o SSO do portal (ver o bloco
de comentários em `api/core/settings.py`), com os papéis invertidos: **aqui o
OCTO é quem emite o ticket, e o `cestaWEB` é quem pergunta "quem é este?"**.

```
  OCTO front                    OCTO API                      cestaWEB
      │                             │                              │
      │ 1. POST /cestas/handoff     │                              │
      │    (JWT normal do usuário)  │                              │
      ├────────────────────────────►│                              │
      │                             │ cria ticket opaco            │
      │ ◄───────────────────────────┤ {ticket, url}                │
      │                             │                              │
      │ 2. abre nova aba: <url>?ticket=…                           │
      ├───────────────────────────────────────────────────────────►│
      │                             │                              │
      │                             │ 3. POST /cestas/handoff/validate
      │                             │◄─────────────────────────────┤
      │                             │  (X-Service-Token + ticket)  │
      │                             │  queima o ticket             │
      │                             ├─────────────────────────────►│
      │                             │  {user, empresa, grant}      │
      │                             │                              │
      │                             │ 4. POST /cestas              │
      │                             │◄─────────────────────────────┤
      │                             │  (X-Service-Token + grant)   │
```

### Por que ticket opaco e não token assinado

Copiando o raciocínio que já está no `settings.py` do OCTO: o ticket **não
carrega dado nenhum**, então não há o que forjar, e a validade é decidida por
quem emitiu. Um JWT assinado exigiria distribuir o `JWT_SECRET` do OCTO para o
Railway — e aí um vazamento no ambiente de fora viraria capacidade de emitir
sessão de qualquer usuário no ambiente de dentro.

### As duas credenciais, e por que não são a mesma

| Credencial | Prova | Vida | Onde mora |
|------------|-------|------|-----------|
| `X-Service-Token` | que a chamada vem do `cestaWEB` | fixa, rotacionável | env var nos dois lados |
| `ticket` | que este usuário acabou de clicar no botão | 5 min, **uso único** | só na URL, nunca gravado no `cestaWEB` |
| `grant` | em nome de quem gravar | 2 h | cookie de sessão do `cestaWEB` |

Regras que valem sempre:

- **Todos** os endpoints exigem `X-Service-Token`.
- `POST /cestas` exige `X-Service-Token` **e** `grant`.
- O dono da cesta vem do `grant`, **nunca** do corpo da requisição. O app de fora
  não escolhe em nome de quem grava.
- Tokens são **opacos** (`secrets.token_urlsafe`), guardados no Postgres do OCTO:
  não podem ser aceitos por engano pelo `get_current_user`, são revogáveis (é uma
  linha no banco) e não exigem segredo compartilhado.

---

## Endpoints

Base: a URL externa da API do OCTO (`OCTO_API_URL` no `cestaWEB`).

### 1. `POST /cestas/handoff` — chamado pelo **front do OCTO**

Único endpoint autenticado pelo JWT normal do usuário (`get_current_user`), não
pelo service token. É o que traduz "estou logado no OCTO" em "posso entrar no
`cestaWEB`".

```jsonc
// requisição: corpo vazio (a identidade vem do JWT)
{}
```

```jsonc
// resposta 201
{
  "ticket": "s7Kd...opaco...",
  "url": "https://cesta.exemplo.com.br/entrar?ticket=s7Kd...",
  "expira_em": "2026-08-12T17:05:00Z"
}
```

- `503` se a feature estiver desligada (`CESTA_WEB_URL` não configurado). O botão
  no front só aparece quando o `GET /cestas/config` diz que está ligada.
- O ticket é de **uso único** e vale 5 minutos. Tempo curto porque o único uso
  legítimo é um redirect imediato.

### 2. `POST /cestas/handoff/validate` — chamado pelo **`cestaWEB`**

```jsonc
// requisição
{ "ticket": "s7Kd..." }
```

```jsonc
// resposta 200
{
  "usuario_id": 42,
  "usuario_nome": "Maria",
  "usuario_email": "maria@cliente.com.br",
  "empresa_nome": "Cliente Exemplo S.A.",
  "grant": "a91c...",
  "grant_expira_em": "2026-08-12T19:00:00Z"
}
```

- O ticket é **queimado** aqui. Revalidar devolve `401`.
- `401` cobre ticket inexistente, expirado e já usado — com a **mesma** mensagem,
  para não informar em qual dos três casos se caiu.
- `empresa_nome` é só para exibir na tela ("subindo cesta para: …"). Não é chave
  de nada — ver *Quem é o dono da cesta*, abaixo.

### 3. `POST /cestas` — chamado pelo **`cestaWEB`**

Grava a cesta. Este é o payload golden.

```jsonc
{
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
      "categoria": "MERCEARIA"
    },
    {
      "ordem": 2,
      "descricao_cliente": "CAFE TORRADO E MOIDO 500G",
      "ean": null,
      "preco": 15.9,
      "marca": null,
      "categoria": null
    }
  ]
}
```

```jsonc
// resposta 201
{
  "cesta_id": 12,
  "total_itens": 2,
  "duplicada": false
}
```

#### Whitelist dos itens

O OCTO lê **exatamente** estes campos de cada item e ignora o resto:

| Campo | Tipo | Obrigatório | Observação |
|-------|------|-------------|------------|
| `ordem` | int ≥ 1 | sim | Ordem do tabloide. É o que define o TOP N. |
| `descricao_cliente` | string | sim | Como veio na planilha, já normalizada. Máx. 500. |
| `preco` | number > 0 | sim | O preço anunciado no tabloide do cliente. |
| `ean` | string \| null | não | **Texto**, nunca número (ver abaixo). |
| `marca` | string \| null | não | Máx. 200. |
| `categoria` | string \| null | não | Máx. 200. |

#### Idempotência

`idempotency_key` = `"sha256:" + sha256(bytes do arquivo)[:64]`.

Se já existir cesta ativa do mesmo dono com a mesma chave, o OCTO **não cria
outra**: responde `200` com `duplicada: true` e o `cesta_id` da que já existe.
Sem isso, duplo clique e retry produzem cestas idênticas no seletor — e isso
acontece na primeira demonstração.

#### Ninguém casa nada com a base neste fluxo

A cesta é **dado de metadados**: uma lista de descrições e preços, gravada no
Postgres do OCTO. É exatamente o que as cestas antigas de `simulation_source.py`
já eram — listas de nome de produto — só que agora em tabela em vez de literal
Python.

Resolver `descricao_cliente` contra a base de mercado é trabalho do **nó da
simulação**, na execução, com a conexão que o grafo já tem (`descricao_produto =
'NM_PRODUTO'` exato, com fallback `ILIKE`). Nem o `cestaWEB` nem a API do OCTO
abrem o banco de dados do cliente para isso:

- o `cestaWEB` não tem acesso nenhum à base de mercado;
- a API do OCTO trata metadados, não dado de cliente;
- e duplicar aquela resolução criaria uma **segunda verdade** sobre o que casou,
  que um dia discordaria do relatório.

Por isso a resposta do `POST /cestas` não fala de casamento. Gravou, acabou.

### 4. `GET /cestas/config` — chamado pelo **front do OCTO**

```jsonc
{ "habilitado": true }
```

Existe para o botão não aparecer em ambiente sem `CESTA_WEB_URL` configurado.
Autenticado pelo JWT normal.

---

## Quem é o dono da cesta

> ⚠️ Decisão tomada contra o código, não contra o desenho ideal.

O plano original dizia "a cesta é da empresa, não do usuário" — para que um
colega da mesma empresa visse a cesta que o outro subiu. Ao implementar,
verificamos que **`users_empresas` nunca recebe uma linha**: a tabela existe no
schema desde a migração inicial, e nenhum ponto do código escreve nela. A única
informação de empresa em uso é `User.empresa`, uma **string livre** digitada no
cadastro (`api/routers/auth.py`).

Chavear a cesta em `empresa_id` seria chavear numa tabela vazia. Então:

- **dono** = `owner_user_id` (funciona hoje, sem ambiguidade);
- `empresa_id` existe na tabela, **nullable**, sem uso — para quando a tenancy
  for populada de verdade, sem migração nova;
- `empresa_texto` guarda o `User.empresa` do momento do upload, como registro.

A regra de **quem vê quais cestas** mora numa função única
(`api/services/cesta_visibilidade.py::cestas_visiveis_para`). Quando
`users_empresas` passar a ser populado, muda essa função e o
compartilhamento liga — sem tocar em schema nem mover dado.

Popular a tenancy de verdade é projeto próprio (migração + admin + backfill de
usuários existentes) e mexe em identidade de toda a plataforma. Não é
efeito colateral de uma feature de cesta.

---

## O modelo de planilha

Vocês definem o formato, então ele é **fechado**: `cestaWEB/app/static/modelo-cesta.xlsx`
é o arquivo que o cliente baixa, e o parser aceita só o que está nele.

| Coluna | Obrigatória | Aceita também |
|--------|-------------|---------------|
| `descricao` | sim | `descrição`, `produto`, `descricao do produto`, `item` |
| `preco` | sim | `preço`, `preco promocional`, `valor` |
| `ean` | não | `codigo de barras`, `gtin`, `codigo` |
| `marca` | não | `fabricante` |
| `categoria` | não | `depto`, `departamento`, `secao` |

O casamento do cabeçalho é **sem acento, sem caixa e sem espaço duplo** — quem
digita "Descrição do Produto" ou "DESCRICAO" é aceito. Coluna desconhecida é
ignorada com aviso na tela.

### EAN é texto, obrigatoriamente

Excel transforma EAN de 13 dígitos em notação científica (`7,89111E+12`) e come
zero à esquerda. Por isso:

1. a coluna do modelo já vem formatada como **texto**;
2. o parser lê o valor cru da célula e, se vier numérico, reconstrói os dígitos
   sem separador nem expoente;
3. EAN que não sobrar com 8, 12, 13 ou 14 dígitos vira `null` **com aviso** — não
   vira lixo silencioso no banco.

Hoje o EAN não casa com nada (a base de mercado não tem coluna de EAN); ele é
guardado para quando tiver. O campo que precisa vir bom é a **descrição**.

---

## O que o `cestaWEB` nunca faz

Lista curta e literal, porque é a fronteira que justifica os dois repos:

- não abre o ClickHouse nem qualquer base de mercado;
- não tem credencial de banco do OCTO — só a URL da API e o service token;
- não lê a tabela `users`; não descobre quais e-mails existem;
- não decide o dono da cesta (vem do grant);
- não guarda o `ticket` (usa e descarta);
- não importa nada de `agentAPI` (é outro repositório, de propósito).

## O que o OCTO nunca faz

- não abre planilha; `openpyxl` não entra no caminho do chat;
- não confia em dono vindo do corpo da requisição;
- não emite JWT de sessão para o `cestaWEB` (só ticket e grant opacos);
- não aceita um token escopado onde espera sessão de usuário (a guarda está em
  `get_current_user`).
