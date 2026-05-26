# smart_auto

Repo: https://github.com/<usuario>sonrib/smart-auto

Monitor automatizado de folha de ponto. Le sistemas de RH externos, detecta marcacoes faltantes e notifica via Telegram (chatbot interativo) e WhatsApp (CallMeBot, somente envio).

## Sistemas suportados

| sufixo | Identificador | Origem                          | Marcacoes/dia              | Dias uteis             |
|--------|---------------|---------------------------------|----------------------------|------------------------|
| `st`   | SANTI         | Senior HCM (Rubi/Conector)      | 4 (07/10/10:15/13)         | seg-qui                |
| `ss`   | Sesa          | Secullum Ponto 4 (ASP.NET)      | 2 (14/20)                  | seg-qui                |
| `eb`   | MentorH       | SIGP EBSERH (Intersystems CSP)  | 2 (07/19 diurno; 19/07 not)| sex+dom + plantao noite|

Marcacoes podem ocorrer antes ou depois dos horarios padrao - o sistema alinha sequencialmente via DP (programacao dinamica) e gera pendencia em dois casos: (a) menos marcacoes que o esperado (faltas) ou (b) mais marcacoes que o esperado (extras). O limiar de extras deriva do `expected` de cada sistema: Senior 4 (5+ = extra), Secullum 2 (3+ = extra), MentorH 2 (3+ = extra). Em `--force` no dia corrente com `expected` filtrado pela carencia, extras nao sao detectados.

Em `/forcar` (dia ou range) que inclua hoje, aplica carencia de 4h: so cobra marcacoes esperadas ate `agora - 4h`. Evita falso positivo enquanto o usuario ainda nao bateu o ponto.

Dias com 0 marcacoes e motivo na folha (atestado, abono, ferias, folga, feriado, DSR, falta) sao tratados como ausencia justificada e nao geram pendencia. Quando o motivo nao casa com token conhecido, o dia segue pendente e a mensagem inclui o texto bruto da ocorrencia para decisao manual.

## Arquitetura

```
+-------------------+        +----------------------+
| cron VM (Oracle)  |        | Telegram chatbot     |
|  sex 15h: --st    |        |  /checar /forcar     |
|  sex 21h: --ss    |        |  /status /resetar    |
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
- Telegram (HTML parse mode, markers de progresso em tempo real)
- WhatsApp via CallMeBot (somente cron, sem interatividade)

## Estrutura de arquivos

| Arquivo                 | Responsabilidade |
|-------------------------|------------------|
| `monitor_ponto.py`      | Orquestrador multi-sistema, avaliacao de pendencias, notificacoes |
| `folha_ponto.py`        | Download/parse da folha Senior (3 requests: EXESENHA -> ENTRANCEREL -> EXEREL) |
| `folha_secullum.py`     | Login + scrape Secullum Ponto 4 (HTTP puro, sem browser) |
| `folha_mentorh.py`      | Login + scrape SIGP EBSERH/MentorH (HTTP puro via %CSP.Broker.cls) |
| `telegram_bot.py`       | Long-poll do Telegram, parse de comandos, streaming de saida |
| `justificativa.py`      | Wrapper diario que dispara `monitor_ponto.py --system st --force-range` em dia 8/21 do mes (com shift quinta+segunda em fim de semana) |
| `provisionar_a1.py`     | Caca capacidade ARM A1 na Oracle e auto-migra |
| `smart-auto-bot.service`| systemd unit do bot |
| `caca-a1.service`       | systemd unit do hunter A1 |
| `deploy/cloud-init.yaml`| Bootstrap inicial da VM (usuario + chave SSH) |
| `.env`                  | Credenciais e tokens (ver `.env.example` abaixo) |
| `monitor_state.json`    | Dias ja notificados, por sistema |

## Setup

### Requisitos

- Python 3.11+
- `pip install requests pdfplumber`
- Conta CallMeBot ativada (envio inicial "I allow callmebot...")
- Bot Telegram criado via @BotFather
- VM Oracle Always Free (E2.1.Micro ou A1.Flex) ou execucao local via Task Scheduler

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
CACA_A1_TELEGRAM_BOT_TOKEN=...  # opcional; se vazio, usa TELEGRAM_BOT_TOKEN
CACA_A1_TELEGRAM_CHAT_ID=...    # opcional; se vazio, usa TELEGRAM_CHAT_ID
CALLMEBOT_PHONE=55XXYYYYYYYYY
CALLMEBOT_APIKEY=...
```

### Execucao local

```powershell
python monitor_ponto.py --system st
python monitor_ponto.py --system ss --no-whatsapp
python telegram_bot.py
```

### VM Oracle

```bash
# crontab -e
0 15 * * 5 cd /home/ubuntu/smart_auto && /home/ubuntu/venv/bin/python monitor_ponto.py --system st
0 21 * * 5 cd /home/ubuntu/smart_auto && /home/ubuntu/venv/bin/python monitor_ponto.py --system ss
0  6 * * 1 cd /home/ubuntu/smart_auto && /home/ubuntu/venv/bin/python monitor_ponto.py --system eb
0 9 * * * cd /home/ubuntu/smart_auto && /home/ubuntu/venv/bin/python justificativa.py --system st   # st: dia 8/21 (shift quinta+segunda se cair fds), range mes corrente
0 16 * * * cd /home/ubuntu/smart_auto && /home/ubuntu/venv/bin/python justificativa.py --system ss   # ss: dia 1, range mes anterior
0 16 * * * cd /home/ubuntu/smart_auto && /home/ubuntu/venv/bin/python justificativa.py --system eb   # eb: dia 1, range mes anterior

sudo systemctl enable --now smart-auto-bot.service
sudo systemctl enable --now caca-a1.service   # opcional, derruba sozinho ao migrar
```

Para separar as mensagens da caca A1 do bot de ponto, crie outro bot no @BotFather
e preencha `CACA_A1_TELEGRAM_BOT_TOKEN` e `CACA_A1_TELEGRAM_CHAT_ID` no `.env` da VM.

## Comandos do bot

Todos exigem sufixo `st`, `ss` ou `eb` (excecao: `/ajuda`).

| Comando                       | Descricao |
|-------------------------------|-----------|
| `/checar st`                  | Roda checagem normal do periodo corrente |
| `/forcar 06/05 ss`            | Avalia um dia especifico |
| `/forcar 20/04-20/05 ss`      | Avalia um range (aceita cross-month) |
| `/status st`                  | Lista dias ja notificados |
| `/resetar ss`                 | Limpa state do sistema |
| `/ajuda`                      | Lista comandos |

Formatos de data aceitos: `DD/MM`, `DDMM`, `DD-MM`, `DDMMAAAA`, `DD MM`.
