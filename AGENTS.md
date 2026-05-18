# AGENTS - smart_auto

Mapa dos componentes autonomos do sistema. Cada "agente" e um processo independente com gatilho proprio.

## 1. Cron Monitor (`monitor_ponto.py --system <st|ss>`)

- **Gatilho**: cron na VM Oracle. `sex 15h` (st) e `sex 21h` (ss).
- **Funcao**: rodar avaliacao do periodo corrente e disparar notificacao dupla (Telegram + WhatsApp) somente se houver pendencias novas.
- **Estado**: le/escreve `monitor_state.json[sys_key]`.
- **Entrada externa**: PDF Senior ou HTML Secullum.
- **Saida externa**: CallMeBot HTTP + Telegram sendMessage.
- **Falha aceitavel**: timeout/login erro -> log + nao marca como notificado, tenta na proxima.

## 2. Telegram Bot (`telegram_bot.py` via `smart-auto-bot.service`)

- **Gatilho**: systemd, long-poll `getUpdates` continuo.
- **Funcao**: receber comandos do dono, validar sufixo, invocar `monitor_ponto.py` como subprocess e streamar progresso.
- **Restricao**: ignora mensagens fora do `TELEGRAM_CHAT_ID` configurado.
- **Modo de parse**: HTML (workaround do bug do Markdown legado que comia primeira linha bold-only).
- **Saida**: mensagens incrementais com markers convertidos em texto humano.

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
- **Notificacao**: Telegram apenas em sucesso ou erro inesperado. Tentativas "out of capacity" sao silenciosas.
- **Custo**: zero - shape sempre dentro do Always Free; `Restart=no` impede loop infinito apos sucesso.

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

## 5. Contratos entre agentes

### Estado compartilhado

`monitor_state.json`:
```json
{
  "st": { "ja_notificados": ["2026-05-04", "2026-05-06"] },
  "ss": { "ja_notificados": ["2026-04-30"] }
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
--force-day DD/MM[/AAAA]    (avalia 1 dia, ignora state)
--force-range DD/MM-DD/MM   (avalia range, ignora state)
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
