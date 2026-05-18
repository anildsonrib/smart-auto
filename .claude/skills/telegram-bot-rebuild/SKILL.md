---
name: telegram-bot-rebuild
description: Guia para reconstruir o chatbot Telegram do smart_auto (long-poll, comandos com sufixo de sistema, streaming de progresso, parsing flexivel de datas). Use ao reimplementar `telegram_bot.py`.
---

# Como reconstruir o bot

## Arquitetura

Long-poll classico contra `getUpdates`:

```python
offset = 0
while True:
    r = requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 30})
    for upd in r.json()["result"]:
        offset = upd["update_id"] + 1
        handle(upd)
```

Filtrar `chat.id != TELEGRAM_CHAT_ID` antes de qualquer acao.

## Comandos

| Comando | Args | Acao |
|---------|------|------|
| `/checar`  | `<st\|ss>`                  | `monitor_ponto.py --system X` |
| `/forcar`  | `<data\|range> <st\|ss>`    | `--force-day` ou `--force-range` |
| `/status`  | `<st\|ss>`                  | le `monitor_state.json[X]` |
| `/resetar` | `<st\|ss>`                  | apaga `monitor_state.json[X]` |
| `/ajuda`   | -                           | lista comandos |

**Sufixo de sistema e obrigatorio.** Sem ele -> mensagem de erro padrao. NAO assumir default.

## Parser de datas

Aceitar formatos comuns sem barra para reduzir friccao no celular:

```python
def parse_data(s):
    raw = s.strip().replace("-", "/").replace(" ", "")
    if "/" not in raw and raw.isdigit():
        if len(raw) == 4: raw = f"{raw[:2]}/{raw[2:]}"
        elif len(raw) in (6, 8): raw = f"{raw[:2]}/{raw[2:4]}/{raw[4:]}"
    # ...parse final com strptime tolerante
```

Range: `parse_range("20/04-20/05")` deve suportar cross-month (entao precisa de ano do contexto).

## Streaming de progresso

`subprocess.Popen` com `stdout=PIPE`, ler linha-a-linha. Quando encontrar marker conhecido, edita mensagem anterior (Telegram `editMessageText`) substituindo por texto humano:

```python
MARCOS = {
    "[cache]":   "Usando cache local",
    "[download]":"Baixando folha",
    "[login]":   "Autenticando",
    "[3/3 EXEREL]": "PDF recebido",
    "[parse]":   "Avaliando marcacoes",
}
```

Resumo final: parsear contagem de pendencias com regex `r"\((\d+) pendencia"`. Quando 0 -> mensagem amigavel ("Ponto OK"), nao "Pendencias ja conhecidas".

## Parse mode

**Use HTML, nao Markdown.** O parser legado do Telegram come a primeira linha quando ela e bold-only (`*SANTI*`), virando string vazia. Converter via:

```python
def md_to_html(t):
    t = re.sub(r"\*(.+?)\*", r"<b>\1</b>", t)
    t = re.sub(r"_(.+?)_", r"<i>\1</i>", t)
    t = re.sub(r"```(.+?)```", r"<pre>\1</pre>", t, flags=re.DOTALL)
    return t
```

Sempre enviar com `parse_mode=HTML`.

## Mensagens de erro

Nunca exponha traceback Python ao usuario. Capture stdout/stderr do subprocess e passe por `friendly_error(raw)` antes de enviar. Mapeie por padrao:

| Sinal no raw                              | Mensagem                                |
|-------------------------------------------|------------------------------------------|
| `HTTPError` + codigo 5xx                  | "Sistema indisponivel (HTTP N)"         |
| HTTP 401/403                              | "Acesso negado — credenciais expiradas" |
| HTTP 404                                  | "URL nao encontrada"                    |
| `ConnectionError` / refused / reset       | "Sem conexao com o sistema"             |
| `getaddrinfo` / `Name or service`         | "DNS falhou"                            |
| `timeout` / `timed out`                   | "Servidor demorou demais"               |
| `SSLError` / `certificate`                | "Erro de certificado SSL"               |
| login + (invalid/incorret/senha)          | "Login invalido — revise .env"          |
| Fallback                                  | `Tipo: msg` em uma linha                |

Extrair o nome amigavel do sistema do raw via regex `\[system\]\s+\S+\s+\(([^)]+)\)` (capta o id como `SANTI`/`Sesa`).

## Menu (setMyCommands)

Registrar uma vez no setup:

```python
requests.post(f"{API}/setMyCommands", json={"commands": [
    {"command": "checar",  "description": "Checa pendencias. Use: /checar st|ss"},
    ...
]})
```

## Renomear bot via API

`setMyName` rejeita form-data com erro UTF-8. Mandar JSON:

```python
requests.post(f"{API}/setMyName",
    headers={"Content-Type": "application/json; charset=utf-8"},
    json={"name": "Notificacao de Ponto"})
```
