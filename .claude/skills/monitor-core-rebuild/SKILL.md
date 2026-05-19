---
name: monitor-core-rebuild
description: Guia para reconstruir o orquestrador `monitor_ponto.py` - dispatcher multi-sistema, alinhamento DP de marcacoes, filtros, state namespaced e CLI. Use ao reimplementar o nucleo do smart_auto.
---

# Reconstruindo o monitor

## Configuracao declarativa

Toda regra especifica de sistema vive em um dict no topo do arquivo:

```python
SYSTEMS = {
    "st": {"id": "SANTI",   "expected": ["07:00","10:00","10:15","13:00"],
           "weekdays": {0,1,2,3}, "downloader": "senior"},
    "ss": {"id": "Sesa",    "expected": ["14:00","20:00"],
           "weekdays": {0,1,2,3}, "downloader": "secullum"},
    "eb": {"id": "MentorH", "expected": ["07:00","19:00"],
           "weekdays": {0,1,2,3,4,5,6}, "downloader": "mentorh"},
}
DELAY_HOURS_AFTER_LAST = 24
FORCE_TODAY_GRACE_HOURS = 4   # /forcar hoje: so cobra horarios <= agora-4h
PDF_CACHE_SECONDS = 600
```

Adicionar sistema = adicionar chave aqui + caso no `baixar_dias()`.

## Alinhamento DP de marcacoes

Trocou a antiga janela de tolerancia (15min) por DP que minimiza distancia total. Motivo: `13:19` vs `13:00` falhava em tolerancia mas e claramente o mesmo turno.

```python
def _alinhar(obs, exp):
    """obs e exp em minutos do dia. Retorna mapeamento i->j ou None se faltar marcacao."""
    if len(obs) > len(exp): return None  # ou trate como extra
    # dp[i][j] = menor custo casando obs[0..i] em exp[0..j]
    ...
```

`faltas_no_dia(punches, expected)` chama `_alinhar` e devolve list de horarios esperados que ficaram sem par.

## Filtros

`force_day` e `force_range` compartilham o mesmo bloco:

```python
def avaliar_dias(dias, sys_cfg, agora, force_day=None, force_range=None):
    hoje = agora.date()
    for d in dias:
        expected_eff = sys_cfg["expected"]
        if force_range or force_day:
            if force_range:
                ini, fim = force_range
                if not (ini <= d["date"] <= fim): continue
            else:
                if d["date"] != force_day: continue
            if d["date"] > hoje: continue
            if d["date"].weekday() not in sys_cfg["weekdays"]: continue
            if not d["is_workday"]: continue
            if d["date"] == hoje:
                limite = agora - timedelta(hours=FORCE_TODAY_GRACE_HOURS)
                expected_eff = [h for h in sys_cfg["expected"]
                                if datetime.combine(d["date"], time(*map(int, h.split(":")))) <= limite]
                if not expected_eff: continue
        else:
            # rotina normal: respeita ja_notificados + DELAY_HOURS_AFTER_LAST
            ...
        faltando = faltas_no_dia(d["punches"], expected_eff)
```

Motivo da carencia: rodar `/forcar` durante o expediente nao deve cobrar marcacoes que o usuario ainda nao teve a chance de bater.

## Override de expected por dia

Sistemas com escala variavel (ex: MentorH 12x36) podem ter `expected` diferente por dia. O downloader injeta `d["expected"]` na lista retornada por `baixar_dias`, e `avaliar_dias` usa `d["expected"]` se presente, senao cai no default do sistema:

```python
expected_eff = d.get("expected") or sys_cfg["expected"]
```

A carencia de 4h continua sendo aplicada sobre `expected_eff`.

## Cache uniforme

Cada downloader mantem cache JSON proprio em `cache/<sistema>_<ini>_<fim>.json` com TTL 600s. Markers obrigatorios:

- `[cache]` no hit
- `[login]` + `[download]` no miss
- `[parse]` emitido pelo monitor apos baixar_dias

Esses sao os pontos que `telegram_bot.py:MARCOS` mapeia para texto humano.

Detector de ausencia em `_has_absence(raw_cells)`: case-insensitive em FERIAS|ABO|ABONO|FALTA|FOLGA|FERIADO|DSR|ATESTADO.

Os 3 dispatchers passam o texto bruto da linha (ocorrencia/obs do Secullum/MentorH, "resto" sem horarios do PDF Senior) para `_has_absence` e tambem populam o campo `motivo` no dict do dia. `avaliar_dias` propaga `motivo` para `pendencias` e `bloco()` adiciona `_Motivo na folha:_` na mensagem quando o dia ficou pendente com 0 marcacoes e ha texto.

## State

`monitor_state.json` namespaced por sistema:

```json
{ "st": {"ja_notificados": [...]}, "ss": {"ja_notificados": [...]}, "eb": {"ja_notificados": [...]} }
```

`load_state(sys_key)` / `save_state(sys_key, dict)`. Migracao de schema antigo (raiz flat) deve ser idempotente.

## CLI

```python
import argparse
p = argparse.ArgumentParser()
p.add_argument("--system", choices=list(SYSTEMS), required=True)
p.add_argument("--force-day")
p.add_argument("--force-range")
p.add_argument("--no-whatsapp", action="store_true")
```

## Cache de PDF/HTML

Salvar download em `cache/<sys>_<periodo>.bin` com mtime; reusar se `now - mtime < PDF_CACHE_SECONDS`. Emitir marker `[cache]` quando usar cache, `[download]` quando baixar.

## Saidas

- `print()` com markers (`[login]`, `[parse]`, etc) para o bot streamar.
- Notificacoes: `notificar_telegram(texto)` e `notificar_whatsapp(texto)` chamados ao final.
- Cabecalho da mensagem: `*{id}*\n<linhas de pendencia>`.
- 0 pendencias com `force_*` ou se chamado pelo cron: mensagem amigavel "Ponto OK em {id}".

## Armadilhas

- Datas relativas em `--force-range` precisam de ano do contexto - default ao ano corrente.
- Cross-month: `20/04-20/05` => `date(2026,4,20)` a `date(2026,5,20)`, varios meses de download.
- Nao gravar em `ja_notificados` quando rodando em `--force-day` (e checagem pontual, nao deve poluir state).
