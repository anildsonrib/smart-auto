---
name: folha-mentorh-rebuild
description: Guia para reconstruir o downloader do espelho de ponto MentorH/SIGP EBSERH (HTTP puro contra Intersystems Cache CSP). Use ao reimplementar `folha_mentorh.py` ou ao adaptar para outro modulo do SIGP.
---

# Reconstruindo folha_mentorh.py

Sistema alvo: portal SIGP EBSERH (`https://sigp.ebserh.gov.br/csp/ebserh/portal/novo/portal.csp`).
Stack do servidor: Intersystems Cache + CSP. RPC AJAX via `%CSP.Broker.cls`. Sessao via cookie `CSPSESSIONID-SP-80-UP-csp-ebserh-`.

## Fluxo end-to-end (sem browser)

### 1. Anti-CSRF tokens (WEVENT)

Toda chamada `cspHttpServerMethod(<TOKEN>, ...)` no JS frontend tem um token unico por sessao, embutido no HTML. Sao **session-bound** (mudam a cada login). Extrair via regex do HTML emitido.

### 2. Login

```python
# a) GET portal.csp -> 302 -> index.csp (cookies de sessao)
r = s.get(f"{BASE}/portal.csp")

# b) Extrai WEVENT da chamada de login
m = re.search(r"mountAjaxData\('([^']+)',\$\('#login'\)\.val\(\)", r.text)

# c) POST Broker.cls
s.post(f"{BASE}/%CSP.Broker.cls",
       headers={"Referer": f"{BASE}/index.csp",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest"},
       data={"WARGC":"3", "WEVENT":wevent,
             "WARG_1":CPF, "WARG_2":SENHA, "WARG_3":""})
# resposta termina com {"Tipo":"S","Descricao":"Seja bem vindo(a)."}
```

### 3. Abrir menu (Folha de Frequencia = 100.3.6.1)

```python
r = s.get(f"{BASE}/portal.csp")
# WEVENT da funcao acessarMenu
m = re.search(r"function\s+acessarMenu\([^)]*\)\s*\{\s*cspHttpServerMethod\('([^']+)'", r.text)

# POST com codigo do menu
r2 = s.post(BROKER, data={"WARGC":"1","WEVENT":wevent_menu,"WARG_1":"100.3.6.1"})
# resposta e JS que seta $('.debug-csp').val('/csp/.../portalRegFrequencia5.csp?CSPToken=XXX')
url_espelho = re.search(r"\.debug-csp'\)\.val\('([^']+)'\)", r2.text).group(1)
```

### 4. Baixar espelho do periodo

```python
# GET inicial pra pegar hidden fields (pagservId, pagservMatr, CSPToken, etc)
r = s.get(HOST + url_espelho, timeout=90)
inputs = dict(re.findall(r"<input[^>]*name=\"([^\"]+)\"[^>]*value=\"([^\"]*)\"", r.text))

# Ajusta periodo em $HOROLOG (dias desde 1840-12-31)
EPOCH = date(1840, 12, 31)
inputs["atualPrimData"] = str((ini - EPOCH).days)
inputs["atualUltData"]  = str((fim - EPOCH).days)

# POST -> HTML com tabela
r2 = s.post(action_url, data=inputs, timeout=90)
```

## Parse da tabela

Cada `<tr>` valido tem 7 colunas: `Dia | (CH) | Horario Trabalhado | H.C. | Saldo | Ocorrencia | Obs`.

```
1 SEX | 0586 | 06:55r - 19:08r | 12:13 | + 00:13 | feriado nacional |
3 DOM | 0586 | 06:57r - 19:01r | 12:00 |          |                    |
15 SEX| 0586 | Afastamento     | 12:00 |          | abono (act)        |
18 SEG|      | Coleta nao realizada |    |          | coleta nao real    |
```

Sufixo `r` = registro REP real. Pares "HH:MMr - HH:MMr" sao entrada/saida. Cross-midnight para plantao noturno: `19:00r - 07:00r`.

## Escala MentorH (caso EBSERH plantao 12x36)

- Regular: sex/dom 07:00-19:00 (diurno)
- Extra: noite em qualquer dia, 19:00 dia X -> 07:00 dia X+1

Como o `expected` do sistema e fixo, injete por dia no dispatcher (`baixar_dias` em `monitor_ponto.py`):

```python
primeira = int(punches[0].split(":")[0]) if punches else None
if primeira is not None and primeira >= 17:
    expected_dia = ["19:00"]          # so a entrada cabe no dia
elif primeira is not None and primeira < 12 and wk not in (4, 6):
    expected_dia = ["07:00"]          # saida de plantao da noite anterior
else:
    expected_dia = ["07:00", "19:00"] # diurno padrao
```

`avaliar_dias` em `monitor_ponto.py` ja respeita `d["expected"]` como override.

## Cache

Mesmo padrao do Secullum: JSON em `cache/mentorh_<ini>_<fim>.json`, TTL 600s. Emitir `[cache]` no hit e `[download]` no miss para o bot pegar via `MARCOS`.

## Armadilhas

- **Path `%CSP.Broker.cls`**: o `%` precisa ser URL-encoded em GET (`%25CSP.Broker.cls`), mas em POST com `requests` passa direto.
- **WEVENT mismatch**: tem varios `mountAjaxData` no HTML; pegue o correto pelo argumento que segue (ex: `$('#login').val()` vs `attr('menu')`). Pegar o errado retorna erro Cache `CLASS DOES NOT EXIST`.
- **Servidor lento via VM cloud**: timeouts >= 90s nas chamadas finais. Local pega em 5-10s, da VM Oracle em ~35s.
- **Senha com `#`**: o `.env` parser deve aceitar valor entre aspas e remover delimitadores (`"hx71bz!#"` -> `hx71bz!#`).
- **Cross-midnight**: o `_alinhar` DP do monitor assume marcacoes em ordem cronologica do dia. Plantao noturno vira 2 linhas no espelho (entrada no dia X, saida no dia X+1) - daí o override de `expected` por dia.
