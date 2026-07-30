# AGENTS - smart_auto

Mapa dos componentes autonomos do sistema. Cada "agente" e um processo independente com gatilho proprio.

## 1. Cron Monitor (`monitor_ponto.py --system <st|ss|eb>`)

- **Gatilho**: systemd timer na VM Oracle (`smart-auto-check@<sys>.timer`, `Persistent=true`), via wrapper `checar_retry.py --scheduled`. `sex 15h` (st), `sex 21h` (ss), `seg 06h` (eb). Nao e cron: se a VM estiver desligada no horario, o timer recupera a execucao no proximo boot. Justificativas (`diario 09h` st, `diario 16h` ss/eb) seguem em cron e chamam `justificativa.py` direto - **essas ainda sao perdidas se a VM estiver desligada no horario**.
- **Funcao**: rodar avaliacao do periodo corrente e disparar notificacao WhatsApp quando houver pendencias vencidas, mesmo se ja notificadas em execucao anterior.
- **Estado**: le/escreve `monitor_state.json[sys_key]`.
- **Entrada externa**: PDF Senior ou HTML Secullum.
- **Saida externa**: CallMeBot HTTP + Telegram sendMessage.
- **Falha aceitavel**: timeout/login erro -> log + alerta WhatsApp de falha; nao marca como notificado. O wrapper registra retry pendente (ver agente 1.5).

## 1.5 Retry de coleta (`checar_retry.py --scheduled|--retry`)

- **Gatilho**: `--scheduled` pelos 3 systemd timers das checagens semanais; `--retry` por cron a cada hora no minuto 30 (`30 * * * *`).
- **Limite conhecido**: o retry cobre **falha de execucao** (exit != 0), nao **ausencia de execucao**. Se o job nunca rodar, nada e registrado. Por isso as checagens semanais usam timer com `Persistent=true` em vez de cron.
- **Funcao**: tornar a checagem resiliente a indisponibilidade do sistema de ponto. `--scheduled` roda o monitor (`--no-state`) e, se falhar (exit != 0), registra o sistema em `retry_state.json`. `--retry` reexecuta de hora em hora apenas os sistemas pendentes (`--no-state --quiet-failure`).
- **Independencia**: por sistema (`st`, `ss`, `eb`) - cada um retenta isolado.
- **Estado**: le/escreve `retry_state.json` (since/attempts/last_error), runtime, fora do git. Escrita atomica + lock local.
- **Saida externa**: nenhuma propria; delega ao monitor. Retentativas sao silenciosas (sem WhatsApp de falha); a 1a falha agendada ja alertou.
- **Parada**: sucesso (exit 0) limpa a entrada; teto `RETRY_MAX_HOURS = 24` descarta entradas antigas sem novo envio. Sem loop interno - 1 tentativa por execucao.
- **Log**: `retry.log` na VM, runtime, fora do git.

## 2. Telegram Bot (`telegram_bot.py` via `smart-auto-bot.service`)

- **Gatilho**: systemd, long-poll `getUpdates` continuo.
- **Funcao**: receber comandos do dono, validar sufixo, invocar `monitor_ponto.py` como subprocess e streamar progresso.
- **Restricao**: ignora mensagens fora do `TELEGRAM_CHAT_ID` configurado.
- **Modo de parse**: HTML (workaround do bug do Markdown legado que comia primeira linha bold-only).
- **Saida**: mensagens incrementais com markers convertidos em texto humano.
- **Erros**: `friendly_error()` traduz traceback em mensagens claras (HTTP 5xx, 401/403, 404, timeout, DNS, conexao recusada, SSL, login invalido, PDF vazio). Fallback exibe so `Tipo: msg` em uma linha.

## 3. Hunter A1 (`provisionar_a1.py` via `caca-a1.service`)

- **Gatilho**: systemd, loop interno a cada `INTERVAL_S = 300`.
- **Funcao**: tentar `LaunchInstance` na Oracle com shape `VM.Standard.A1.Flex` (4 OCPU + 24 GB hardcoded).
- **Pre-check**: se ja existe instancia A1 ativa, aborta para evitar duplicacao.
- **Pos-sucesso**:
  1. aguarda SSH na nova VM
  2. instala deps + copia arquivos
  3. instala `smart-auto-bot.service` + crontab
  4. para o bot na VM E2 (evita duplo polling)
  5. `systemctl disable caca-a1` (self-shutdown)
- **Notificacao**: Telegram apenas em sucesso ou erro inesperado. Usa `CACA_A1_TELEGRAM_BOT_TOKEN`/`CACA_A1_TELEGRAM_CHAT_ID` quando configurados; caso contrario, usa o bot principal. Tentativas "out of capacity" sao silenciosas.
- **Custo**: zero - shape sempre dentro do Always Free; `Restart=no` impede loop infinito apos sucesso.

## 3.5 Wrapper de justificativa (`justificativa.py --system <st|ss|eb>`)

- **Gatilhos**: 3 crons distintos na VM.
  - `0 9 * * * --system st`: justificativa Santi RH (manual, exige presenca). Dias 8 e 21; weekend shift = quinta+segunda. Range: dia 20 do mes anterior ao dia 20 do mes corrente (ciclo do RH; futuras ignoradas).
  - `0 16 * * * --system ss`: justificativa Sesa (online). Dia 1 sem shift. Range: mes anterior.
  - `0 16 * * * --system eb`: justificativa MentorH (online). Dia 1 sem shift. Range: mes anterior.
- **Regras**: definidas no dict `RULES` (days, weekend_shift, periodo, subtitulo) dentro de `justificativa.py`.
- **Acao**: chama `monitor_ponto.py --system <sys> --force-range INI FIM --subtitulo "..."`. `--force-range` bypassa dedup mas preserva carencia de 4h e deteccao de motivo na folha. Sem pendencia => nenhuma mensagem enviada.
- **Saida**: WhatsApp via CallMeBot (monitor_ponto nao envia Telegram em modo cron).
- **Log**: `/home/ubuntu/smart_auto/justificativa.log`.

## 4. Downloaders

### 4.1 Senior (`folha_ponto.py`)

3 requests em sequencia contra Rubi/Conector:
1. `EXESENHA` - autenticacao
2. `ENTRANCEREL` - prepara relatorio
3. `EXEREL` - baixa PDF

Parse via `pdfplumber`. Cache de 10 min em disco.

### 4.2 Secullum (`folha_secullum.py`)

HTTP puro contra ASP.NET WebForms:
1. GET `pgLogin.aspx` -> captura `__VIEWSTATE` + `__VIEWSTATEGENERATOR`
2. POST com `__EVENTTARGET=lnkLogin` + credenciais
3. GET `pgCalculos.aspx` (novo VIEWSTATE)
4. POST com `__EVENTTARGET=ctl00$ContentPlaceHolder1$lnkAtualizar` + periodo

Parse via regex (`<tr>` -> `<td>`, stripping de tags aninhadas `<a><font>`).

### 4.3 MentorH (`folha_mentorh.py`)

HTTP puro contra Intersystems Cache CSP:
1. GET `/portal/novo/portal.csp` -> 302 `/index.csp` (cookies de sessao)
2. Extrai WEVENT de `mountAjaxData('<TOKEN>', $('#login').val()...)` no HTML
3. POST `%CSP.Broker.cls` com `WARGC=3&WEVENT=...&WARG_1=cpf&WARG_2=senha&WARG_3=`
4. GET `/portal.csp` autenticado -> extrai WEVENT de `function acessarMenu`
5. POST `%CSP.Broker.cls` com `WARG_1=100.3.6.1` -> resposta JS contem URL do espelho com CSPToken
6. GET espelho (hidden fields), POST com `atualPrimData`/`atualUltData` em `$HOROLOG` (dias desde 1840-12-31)

Parse via regex de `<tr>`/`<td>`. Detecta turno por dia (diurno sex/dom vs noturno extra) e injeta `expected` por dia no dispatcher.

### 4.4 Deteccao de motivo na folha

Quando um dia tem 0 marcacoes, o dispatcher inspeciona o texto bruto da linha (ocorrencia/obs) e:
- Marca `is_workday=False` se casar com `_ABSENCE_TOKENS` (FERIAS, ABO, ABONO, FALTA, FOLGA, FERIADO, DSR, ATESTADO) - vale pros 3 downloaders.
- Propaga o texto via campo `motivo`. A mensagem final do bot mostra `_Motivo na folha:_` quando o dia ficou pendente mesmo com 0 marcacoes e ha texto bruto - util pra casos onde o motivo nao casa com token (ex: "Coleta nao realizada", "Compensacao XYZ").

### 4.5 Cache uniforme

Todos os downloaders mantem cache JSON em `cache/<sistema>_<ini>_<fim>.json` com TTL 600s. Hit emite `[cache]`; miss emite `[login]` + `[download]`. Senior tambem mantem cache do PDF cru em `folha_ponto_*.pdf` para debug.

## 5. Contratos entre agentes

### Estado compartilhado

`monitor_state.json`:
```json
{
  "st": { "ja_notificados": ["2026-05-04", "2026-05-06"] },
  "ss": { "ja_notificados": ["2026-04-30"] },
  "eb": { "ja_notificados": ["2026-05-08"] }
}
```

### Markers de progresso (subprocess -> bot)

| Marker            | Mensagem amigavel       |
|-------------------|--------------------------|
| `[cache]`         | Usando cache local       |
| `[download]`      | Baixando folha           |
| `[login]`         | Autenticando             |
| `[3/3 EXEREL]`    | PDF recebido             |
| `[parse]`         | Avaliando marcacoes      |

### CLI do `monitor_ponto.py`

```
--system st|ss              (obrigatorio)
--force-day YYYY-MM-DD      (avalia 1 dia, ignora state; hoje usa carencia 4h)
--force-range YYYY-MM-DD YYYY-MM-DD  (avalia range, ignora state; hoje usa carencia 4h)
--no-whatsapp               (suprime CallMeBot)
```

## 6. Adicionar um novo sistema

1. Implementar `folha_<nome>.py` expondo `baixar(ini, fim) -> list[dict]` com chaves `{date, marcacoes, raw}`.
2. Adicionar entrada em `SYSTEMS` dentro de `monitor_ponto.py`:
   ```python
   "xx": {
       "id": "Identificador",
       "expected": ["HH:MM", ...],
       "weekdays": {0,1,2,3,4},
       "downloader": "<nome>",
   }
   ```
3. Adicionar despacho em `baixar_dias()`.
4. Adicionar label em `SYS_LABELS` no `telegram_bot.py`.
5. Adicionar cron na VM se quiser checagem automatica.
