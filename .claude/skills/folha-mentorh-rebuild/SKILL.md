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

### 4. Baixar espelho do periodo (grid DataTables server-side, desde jul/2026)

A pagina do espelho NAO traz mais a tabela no HTML (antes era POST do form com
`atualPrimData`/`atualUltData` em $HOROLOG + parse de `<tr>`). Desde jul/2026 o
grid e DataTables `serverSide: true` carregado por hyperevent CSP:

```python
# GET da pagina do espelho -> extrai token do grid e matricula
r = s.get(HOST + url_espelho, timeout=90)
m = re.search(r"cspHttpServerMethod\('([^']+)',JSON\.stringify\(data\),'(\d+)'", r.text)
token, matricula = m.groups()

# broker RELATIVO ao diretorio da pagina (cspxmlhttp.js usa URL relativa)
broker = url_espelho.split("?")[0].rsplit("/", 1)[0] + "/%CSP.Broker.cls"

# request DataTables + competencia MM/AAAA (espelho e mensal)
cols = [{"data": c, "name": "", "searchable": True, "orderable": False,
         "search": {"value": "", "regex": False}}
        for c in ("Dia","CHCodigo","HorarioTrabalhado","CHConsiderada","Saldo","Ocorrencia")]
dt_req = {"draw": 1, "columns": cols, "order": [], "start": 0,
          "length": 31, "search": {"value": "", "regex": False}}
r2 = s.post(broker, timeout=300,   # servidor leva ~90s para montar o espelho
            data={"WARGC": "3", "WEVENT": token, "WARG_1": json.dumps(dt_req),
                  "WARG_2": matricula, "WARG_3": f"{ini.month:02d}/{ini.year}"})
```

## Parse da resposta do hyperevent

A resposta e texto CSP: linhas de controle, marcador `#OK` e o JSON em seguida.

```python
payload = r2.text[r2.text.find("#OK") + 3:].strip()
rows = json.loads(payload)["data"]
```

Cada row: `{"Dia": "03  sex", "CHCodigo": 586, "HorarioTrabalhado": "07:05r - 19:15r",
"CHConsiderada": "12:10", "Saldo": " + 00:10", "Ocorrencia": "", "mes_ano": "07/2026", ...}`.
Dias sem batida trazem `Ocorrencia: "Coleta Não Realizada"`. Reconstruir `raw` na
ordem `[Dia, CHCodigo, HorarioTrabalhado, CHConsiderada, Saldo, Ocorrencia]` para
manter o contrato com `monitor_ponto.py` (cells[1]=CH, cells[2]=horarios/justificativa).

Guarda de competencia: validar `mes_ano` de cada row contra o MM/AAAA solicitado
(e o dia-da-semana da coluna `Dia` contra a data calculada) - competencia divergente
geraria plantoes/extras fantasmas; falhar explicito.

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
- **Servidor lento via VM cloud**: o hyperevent do grid leva ~90s para responder (o servidor monta o espelho sob demanda); usar timeout >= 300s. Uma resposta que "trava" nao e erro - e espera normal.
- **Pagina esqueleto (~5 KB) com 0 linhas parseadas**: sinal de que o fluxo antigo (POST do form) esta sendo usado contra o grid novo - o HTML so tem o setup do DataTables; os dados vem exclusivamente pelo hyperevent.
- **Senha com `#`**: o `.env` parser deve aceitar valor entre aspas e remover delimitadores (`"hx71bz!#"` -> `hx71bz!#`).
- **Cross-midnight**: o `_alinhar` DP do monitor assume marcacoes em ordem cronologica do dia. Plantao noturno vira 2 linhas no espelho (entrada no dia X, saida no dia X+1) - daí o override de `expected` por dia.
