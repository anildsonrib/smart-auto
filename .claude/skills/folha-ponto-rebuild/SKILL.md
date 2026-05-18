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

Use DP - NAO janela de tolerancia fixa. Motivo: 15min de tolerancia falha em casos como `13:19` vs esperado `13:00`. O usuario tem N marcacoes fixas por dia; se observou < N, ha pendencia, independente do horario.

```python
def _alinhar(obs, exp):
    # DP minimizando soma de |obs[i] - exp[j]| com obs[i] casando esp[j]
    # retorna mapeamento i->j ou None se len(obs) < len(exp)
```

## Filtros pre-notificacao

- `if d["date"] > hoje: continue`
- `if d["date"].weekday() not in weekdays: continue`
- `if _has_absence(raw): continue` (FERIAS/ABO/FOLGA/FERIADO)
- `if hoje == d["date"] and now < ultima_esperada + 24h: continue`

## Armadilhas conhecidas

- Senior expira sessao em ~10min; nao reutilizar entre runs separadas.
- Secullum retorna 200 mesmo em login falho - validar redirecionando para `pgLogin.aspx` ou presenca de `lnkLogin` no HTML.
- HTML do Secullum nao tem charset declarado; forcar `response.encoding = "utf-8"` se acentos vierem quebrados.
