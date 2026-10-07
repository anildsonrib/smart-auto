# smart_auto

Repo: https://github.com/<usuario>sonrib/smart-auto

Monitor automatizado de folha de ponto. Le sistemas de RH externos, detecta marcacoes faltantes e notifica via Telegram (chatbot interativo). O canal WhatsApp (CallMeBot) existe no codigo mas esta desligado por opcao do usuario (`WHATSAPP_ENABLED=false` no `.env`).

## Sistemas suportados

| sufixo | Identificador | Origem                          | Marcacoes/dia              | Dias uteis             |
|--------|---------------|---------------------------------|----------------------------|------------------------|
| `st`   | SANTI         | Senior HCM (Rubi/Conector)      | 4 (07/10/10:15/13) + marcações inválidas se intervalo < 15 min | seg-qui                |
| `ss`   | Sesa          | Secullum Ponto 4 (ASP.NET)      | 2 (14/20)                  | seg-qui                |
| `eb`   | MentorH       | SIGP EBSERH (Intersystems CSP)  | 2 (07/19 diurno; 19/07 not)| sex+dom + plantao noite|

Marcacoes podem ocorrer antes ou depois dos horarios padrao - o sistema alinha sequencialmente via DP (programacao dinamica) e gera pendencia em dois casos: (a) menos marcacoes que o esperado (faltas) ou (b) mais marcacoes que o esperado (extras). O limiar de extras deriva do `expected` de cada sistema: Senior 4 (5+ = extra), Secullum 2 (3+ = extra), MentorH 2 (3+ = extra). Em `--force` no dia corrente com `expected` filtrado pela carencia, extras nao sao detectados.

No `st`, qualquer intervalo entre batidas menor que 15 minutos tambem gera pendencia de marcações inválidas.

Em `/forcar` (dia ou range) que inclua hoje, aplica carencia de 4h: so cobra marcacoes esperadas ate `agora - 4h`. Evita falso positivo enquanto o usuario ainda nao bateu o ponto.

Dias com 0 marcacoes e motivo na folha (atestado, abono, ferias, folga, feriado, DSR, falta) sao tratados como ausencia justificada e nao geram pendencia. Quando o motivo nao casa com token conhecido, o dia segue pendente e a mensagem inclui o texto bruto da ocorrencia para decisao manual.

**Feriados** nao dependem da folha: `feriados.py` resolve por calendario proprio
(BrasilAPI -> cache local -> calculo da Pascoa como fallback), porque o Secullum
nao marca feriado nas celulas e 07/09/2026 era cobrado como falta. Vale para `st`
e `ss` (diarista); `eb` nao usa, pois plantonista trabalha feriado.

**Janela avaliada** pela checagem automatica segue o ciclo de competencia do
sistema (`ciclo` em `SYSTEMS`): `st` usa 21 -> 20 (competencia do RH Senior),
`ss` e `eb` usam mes civil. Com mes civil no `st`, uma pendencia de 23/09 saia do
radar em 01/10 embora o ciclo so fechasse em 20/10.

## Arquitetura

```
+-------------------+        +----------------------+
| systemd timers VM |        | Telegram chatbot     |
|  sex 15h: --st    |        |  /checar /forcar     |
|  sex 21h: --ss    |        |  /status /resetar    |
|  seg 06h: --eb    |        |                      |
+--------+----------+        +-----------+----------+
         |                               |
         v                               v
  +-------------------------------------------+
  |               monitor_ponto.py            |
  | - dispatcher por sistema (SYSTEMS dict)   |
  | - state namespaced {"st":..., "ss":...}   |
  | - DP de alinhamento de marcacoes          |
  | - filtro de dia util e data futura        |
  +------+-------------------+----------------+
         |                   |
         v                   v
  folha_ponto.py     folha_secullum.py
  (Senior 3-step)    (ASP.NET WebForms)
         |                   |
         v                   v
        PDF                 HTML
   (pdfplumber)            (regex)
```

Saidas:
- Telegram (HTML parse mode, markers de progresso em tempo real) - **canal unico**
- WhatsApp via CallMeBot: desligado (`WHATSAPP_ENABLED=false`); reativavel pelo `.env`

## Estrutura de arquivos

| Arquivo                 | Responsabilidade |
|-------------------------|------------------|
| `monitor_ponto.py`      | Orquestrador multi-sistema, avaliacao de pendencias, notificacoes |
| `folha_ponto.py`        | Download/parse da folha Senior (3 requests: EXESENHA -> ENTRANCEREL -> EXEREL) |
| `folha_secullum.py`     | Login + scrape Secullum Ponto 4 (HTTP puro, sem browser) |
| `folha_mentorh.py`      | Login + scrape SIGP EBSERH/MentorH (HTTP puro via %CSP.Broker.cls) |
| `telegram_bot.py`       | Long-poll do Telegram, parse de comandos, streaming de saida |
| `justificativa.py`      | Wrapper diario que dispara `monitor_ponto.py --system st --force-range` em dia 7/20 do mes (com shift quinta+segunda em fim de semana). Com catch-up: disparo perdido e recuperado por até `CATCHUP_MAX_DAYS` |
| `justificativa_state.json` | Datas de disparo de justificativa ja cumpridas, por sistema; fora do git |
| `feriados.py`           | Calendario de feriados independente da folha (BrasilAPI + cache + fallback local) |
| `feriados_cache.json`   | Cache anual da BrasilAPI, revalidado a cada 90 dias; fora do git |
| `feriados_extra.json`   | Feriados estaduais/municipais declarados a mao (opcional); fora do git |
| `checar_retry.py`       | Wrapper de cron: `--scheduled` roda a checagem e registra retry em caso de falha; `--retry` reexecuta de hora em hora os sistemas pendentes (`--quiet-failure`) |
| `retry_state.json`      | Estado runtime do retry por sistema (since/attempts/last_error); fora do git |
| `smart-auto-bot.service`| systemd unit do bot |
| `smart-auto-check@.service` | systemd unit template das checagens semanais (instanciada por sistema: `@st`, `@ss`, `@eb`) |
| `smart-auto-check@<sys>.timer` | timers das checagens com `Persistent=true` (recupera execucao perdida se a VM estava desligada) |
| `smart-auto-justificativa@.service` | systemd unit template das justificativas diarias |
| `smart-auto-justificativa@<sys>.timer` | timers diarios das justificativas (`Persistent=true`) |
| `docs/SPEC-correcoes-checagem.md` | Spec das correcoes de confiabilidade de 07/10/2026 |
| `deploy/52unattended-upgrades-local` | politica de reboot automatico do kernel (04:15) |
| `deploy/cloud-init.yaml`| Bootstrap inicial da VM (usuario + chave SSH) |
| `.env`                  | Credenciais e tokens (ver `.env.example` abaixo) |
| `monitor_state.json`    | Dias ja notificados, por sistema |

## Setup

### Requisitos

- Python 3.11+
- `pip install requests pdfplumber`
- Conta CallMeBot ativada (opcional; so se for reativar o WhatsApp)
- Bot Telegram criado via @BotFather
- VM Oracle Always Free (E2.1.Micro) ou execucao local via Task Scheduler

### `.env`

```
WAAPI_USERNAME=...
WAAPI_PASSWORD=...
SECULLUM_BASE_URL=http://.../Ponto4/
SECULLUM_USERNAME=...
SECULLUM_PASSWORD=...
MENTORH_BASE_URL=https://sigp.ebserh.gov.br/csp/ebserh/portal/novo/portal.csp
MENTORH_USERNAME=...
MENTORH_PASSWORD=...   # entre aspas se contiver `#`
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
CALLMEBOT_PHONE=55XXYYYYYYYYY   # so usado se WHATSAPP_ENABLED=true
CALLMEBOT_APIKEY=...
WHATSAPP_ENABLED=false          # canal WhatsApp desligado; notifica so no Telegram
```

### Execucao local

```powershell
python monitor_ponto.py --system st
python monitor_ponto.py --system ss --no-whatsapp
python telegram_bot.py
```

### VM Oracle

Checagens e justificativas rodam via **systemd timer** (nao cron), para nao serem
perdidas se a VM estiver desligada no horario. So o retry horario segue em cron.

```bash
# checagens semanais + justificativas: timers com Persistent=true
sudo cp 'smart-auto-check@.service' 'smart-auto-check@'*.timer /etc/systemd/system/
sudo cp 'smart-auto-justificativa@.service' 'smart-auto-justificativa@'*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now 'smart-auto-check@st.timer'   # sex 15h (Senior)
sudo systemctl enable --now 'smart-auto-check@ss.timer'   # sex 21h (Secullum)
sudo systemctl enable --now 'smart-auto-check@eb.timer'   # seg 06h (MentorH, cobre plantao de domingo)
sudo systemctl enable --now 'smart-auto-justificativa@st.timer'   # diario 09h
sudo systemctl enable --now 'smart-auto-justificativa@ss.timer'   # diario 16h
sudo systemctl enable --now 'smart-auto-justificativa@eb.timer'   # diario 16h

# crontab -e  (unica linha restante)
30 * * * * cd /home/ubuntu/smart_auto && /home/ubuntu/venv/bin/python checar_retry.py --retry >> ~/smart_auto/retry.log 2>&1   # retry horario (min 30): so reexecuta sistemas pendentes

sudo systemctl enable --now smart-auto-bot.service

# politica de reboot automatico do kernel (04:15, sem colisao com nenhum job)
sudo cp deploy/52unattended-upgrades-local /etc/apt/apt.conf.d/
```

#### Por que timer e nao cron

Se a VM estiver desligada quando o cron dispara, o job simplesmente nao roda - e o
`checar_retry.py` **nao** cobre isso, porque ele registra falha de execucao
(exit != 0), nao ausencia de execucao. O job seria perdido em silencio.

Nas **checagens**, `Persistent=true` resolve sozinho: o systemd grava o ultimo
disparo em `/var/lib/systemd/timers/stamp-smart-auto-check@<sys>.timer` e executa o
job atrasado no proximo boot. Verificado em 2026-07-30 retroagindo o stamp em 14
dias: o timer disparou imediatamente ao ser reiniciado.

Nas **justificativas**, o timer sozinho **nao** bastaria: o `justificativa.py` roda
todo dia e decide internamente se hoje e dia 7/20 (st) ou dia 1 (ss/eb), entao um
catch-up no dia seguinte cairia no gate "nao e o dia" e nao faria nada. Por isso o
catch-up e implementado no proprio script, via `justificativa_state.json` (ver
docstring do modulo): cada data de disparo e um evento, marcado como cumprido so
apos exit 0, e o range e calculado a partir da data de disparo - nao de hoje.

#### Retry horario (falha de coleta)

As checagens semanais rodam via `checar_retry.py --scheduled --system <st|ss|eb>`. Se o sistema de ponto estiver fora do ar, lento ou inacessivel, o monitor falha (exit != 0) e o wrapper registra o sistema em `retry_state.json`. A linha `30 * * * * checar_retry.py --retry` reexecuta de hora em hora (no minuto 30) apenas os sistemas pendentes, ate conseguir.

- Vale para `st`, `ss` e `eb`, de forma independente por sistema.
- A primeira falha agendada ainda dispara o alerta de falha no canal ativo (uma vez).
- As retentativas horarias rodam com `--quiet-failure`: silenciosas, sem alerta de falha a cada hora.
- No sucesso, a pendencia (se houver) e enviada normalmente e a entrada de retry e removida.
- Teto de `RETRY_MAX_HOURS = 24`: entradas mais antigas sao descartadas sem novo envio.
- `retry_state.json` e `retry.log` sao estado/log de runtime na VM, fora do git.

## Comandos do bot

Todos exigem sufixo `st`, `ss` ou `eb` (excecao: `/ajuda`).

| Comando                       | Descricao |
|-------------------------------|-----------|
| `/checar st`                  | Roda checagem do ciclo corrente do sistema, respeita 24h e ignora dedup por state. **Nao aceita data** - rejeita com erro e sugere o `/forcar` equivalente |
| `/forcar 06/05 ss`            | Avalia um dia especifico |
| `/forcar 20/04-20/05 ss`      | Avalia um range (aceita cross-month) |
| `/status st`                  | Lista dias ja notificados |
| `/resetar ss`                 | Limpa state do sistema |
| `/ajuda`                      | Lista comandos |

Formatos de data aceitos: `DD/MM`, `DDMM`, `DD-MM`, `DDMMAAAA`, `DD MM`.
