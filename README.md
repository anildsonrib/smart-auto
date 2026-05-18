# smart_auto

Repo: https://github.com/<usuario>sonrib/smart-auto

Monitor automatizado de folha de ponto. Le sistemas de RH externos, detecta marcacoes faltantes e notifica via Telegram (chatbot interativo) e WhatsApp (CallMeBot, somente envio).

## Sistemas suportados

| sufixo | Identificador | Origem                     | Marcacoes/dia      | Dias uteis  |
|--------|---------------|----------------------------|---------------------|-------------|
| `st`   | SANTI         | Senior HCM (Rubi/Conector) | 4 (07/10/10:15/13)  | seg-qui     |
| `ss`   | Sesa          | Secullum Ponto 4 (ASP.NET) | 2 (14/20)           | seg-qui     |

Marcacoes podem ocorrer antes ou depois dos horarios padrao - o sistema alinha sequencialmente via DP (programacao dinamica) e considera pendencia somente quando o numero de marcacoes do dia for menor que o esperado.

Em `/forcar` (dia ou range) que inclua hoje, aplica carencia de 4h: so cobra marcacoes esperadas ate `agora - 4h`. Evita falso positivo enquanto o usuario ainda nao bateu o ponto.

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
| `telegram_bot.py`       | Long-poll do Telegram, parse de comandos, streaming de saida |
| `provisionar_a1.py`     | Caca capacidade ARM A1 na Oracle e auto-migra |
| `smart-auto-bot.service`| systemd unit do bot |
| `caca-a1.service`       | systemd unit do hunter A1 |
| `cloud-init.yaml`       | Bootstrap inicial da VM (usuario + chave SSH) |
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
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
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

sudo systemctl enable --now smart-auto-bot.service
sudo systemctl enable --now caca-a1.service   # opcional, derruba sozinho ao migrar
```

## Comandos do bot

Todos exigem sufixo `st` ou `ss` (excecao: `/ajuda`).

| Comando                       | Descricao |
|-------------------------------|-----------|
| `/checar st`                  | Roda checagem normal do periodo corrente |
| `/forcar 06/05 ss`            | Avalia um dia especifico |
| `/forcar 20/04-20/05 ss`      | Avalia um range (aceita cross-month) |
| `/status st`                  | Lista dias ja notificados |
| `/resetar ss`                 | Limpa state do sistema |
| `/ajuda`                      | Lista comandos |

Formatos de data aceitos: `DD/MM`, `DDMM`, `DD-MM`, `DDMMAAAA`, `DD MM`.
