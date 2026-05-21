---
name: folha-ponto-rebuild
description: Guia para reconstruir do zero o pipeline de download e parse de folhas de ponto (Senior HCM via Rubi/Conector e Secullum Ponto 4 via ASP.NET WebForms). Use quando precisar reimplementar `folha_ponto.py` ou `folha_secullum.py`.
---

# Como reconstruir os downloaders

## Senior HCM (sufixo `st`)

Sequencia obrigatoria de 3 POSTs contra o endpoint Rubi/Conector (sessao mantida por cookie):

1. **EXESENHA** - login. Body com usuario + senha. Resposta seta cookie de sessao.
2. **ENTRANCEREL** - "entra" no relatorio de folha. Prepara contexto server-side. Sem isso o EXEREL responde vazio.
3. **EXEREL** - download do PDF binario. Aceitar `application/pdf`.

Parse: `pdfplumber.open()` -> iterar paginas -> `extract_table()`. Cada linha vira `{date, marcacoes[], raw[]}`.

### Coluna Originais vs Tratadas

Cada linha do PDF tem `Originais (N)`, `Tratadas (N)`, `Trabalho (1)` e opcionais. Tratadas e o que o RH considera valido (Originais ajustadas).

- **Caso normal**: Originais == Tratadas. Heuristica simetrica: maior N onde `horas[:N] == horas[N:2N]` sao as marcacoes.
- **Caso ajustado por RH**: usuario nao bateu todos os pontos e RH preencheu via tratativa -> `len(Originais) < len(Tratadas)`. Simetrica falha. Fallback: dropar o ultimo valor (Trabalho) e pegar os ultimos `expected_count` da sequencia restante (== Tratadas). Por isso `parse_pdf` precisa de `expected_count=len(cfg["expected"])`.

Exemplo concreto: linha `06/05 QUA 0260 06:56 13:05 06:56 10:00 10:15 13:05 05:45` (Senior, expected=4). Originais=[06:56,13:05], Tratadas=[06:56,10:00,10:15,13:05], Trabalho=05:45. Sem o fallback, retornaria 0 marcacoes e falso-positivo "todas faltando".

Cache: salvar PDF em `cache/senior_<YYYYMM>.pdf` com mtime; reusar se < 600s.

## Secullum Ponto 4 (sufixo `ss`)

ASP.NET WebForms exige preservar `__VIEWSTATE` e `__VIEWSTATEGENERATOR` entre requests. Toda interacao e `__doPostBack`.

1. GET `Paginas/pgLogin.aspx` -> extrair hidden inputs via regex `<input[^>]+name="X"[^>]+value="([^"]*)"`.
2. POST mesma URL com:
   - `__EVENTTARGET=lnkLogin`
   - `cboModoLogin=0`
   - `txtUsuario`, `txtSenha`
   - hiddens capturados
3. GET `Paginas/pgCalculos.aspx` -> novo VIEWSTATE.
4. POST com:
   - `__EVENTTARGET=ctl00$ContentPlaceHolder1$lnkAtualizar`
   - `ctl00$ContentPlaceHolder1$txtPeriodoIni/Fim` em `DD/MM/YYYY`
   - checkboxes (`chkTOTAIS_RODAPE`, etc) como `"on"`

Parse HTML: split por `<tr>`, depois `<td>`, depois strip de tags. Atencao: celulas tem `<a><font>` aninhado - precisa stripping recursivo, nao apenas extracao direta de texto entre `<td>`.

## Alinhamento de marcacoes (compartilhado)

Use DP - NAO janela de tolerancia fixa. Motivo: 15min de tolerancia falha em casos como `13:19` vs esperado `13:00`. O usuario tem N marcacoes fixas por dia; gera pendencia se observou < N (faltas) OU > N (extras).

```python
def _alinhar(obs, exp):
    # DP minimizando soma de |obs[i] - exp[j]| com obs[i] casando esp[j]
    # retorna mapeamento i->j ou None se len(obs) < len(exp)
```

Faltas: `_alinhar(punches, expected)`, esperadas nao cobertas viram falta. Extras: `_alinhar(expected, punches)` (papeis trocados), punches nao cobertas sao as extras. Em `--force` no dia corrente com `expected` reduzido pela carencia, extras nao sao computadas (evita falso-positivo).

## Filtros pre-notificacao

- `if d["date"] > hoje: continue`
- `if d["date"].weekday() not in weekdays: continue`
- `if _has_absence(raw): continue` (FERIAS/ABO/ABONO/FALTA/FOLGA/FERIADO/DSR/ATESTADO)
- Para o Senior: o texto da ocorrencia esta na propria linha do PDF apos o codigo horario (ex: `14/04 TER 0260 Atestado nao Rel.ao Trabalho`). Extrair removendo horarios `HH:MM` do `resto`, passar pro `_has_absence` e tambem propagar como `motivo` no dict do dia (a mensagem final mostra esse texto quando o dia segue pendente com 0 marcacoes).
- `if hoje == d["date"] and now < ultima_esperada + 24h: continue`

## Armadilhas conhecidas

- Senior expira sessao em ~10min; nao reutilizar entre runs separadas.
- Secullum retorna 200 mesmo em login falho - validar redirecionando para `pgLogin.aspx` ou presenca de `lnkLogin` no HTML.
- HTML do Secullum nao tem charset declarado; forcar `response.encoding = "utf-8"` se acentos vierem quebrados.
