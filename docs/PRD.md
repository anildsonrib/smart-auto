# PRD - smart_auto

## 1. Problema

Funcionarios precisam acompanhar suas marcacoes de ponto em multiplos sistemas (Senior HCM, Secullum Ponto 4) e arriscam descontos / advertencias quando esquecem batidas. A checagem manual e tediosa, especialmente quando ha mais de um sistema com horarios e regras distintos.

## 2. Objetivo

Automatizar a deteccao de marcacoes faltantes e entregar notificacao pro-ativa em canais convenientes (Telegram, WhatsApp), com possibilidade de checagem on-demand e cobertura de janelas customizadas.

## 3. Usuario alvo

Profissional unico (uso pessoal), bilingue PT-BR, opera Windows + VM Linux, valoriza:
- Custo zero (Oracle Always Free)
- Operacao autonoma (cron + chatbot sem PC ligado)
- Mensagens curtas, sem emoji desnecessario, sem hype

## 4. Requisitos funcionais

### 4.1 Multi-sistema

- Suportar N sistemas de ponto via dicionario `SYSTEMS = {sufixo: {...}}`
- Cada sistema define: identificador visivel, horarios esperados, dias uteis, downloader
- State persistido por sistema (chaves atuais: `st`/`ss`/`eb`)
- Comandos no chatbot exigem sufixo explicito - sem default
- Cada downloader expoe `baixar(ini, fim) -> list[dict]` e mantem cache JSON de 600s em `cache/`

### 4.2 Avaliacao de pendencias

- DP de alinhamento sequencial entre marcacoes observadas e esperadas
- Gera pendencia em dois casos: `len(observadas) < len(esperadas)` (faltas) ou `len(observadas) > len(esperadas)` (extras). Limiar deriva de `expected` por sistema (st=4, ss=2, eb=2). Em `--force` no dia corrente com `expected` filtrado pela carencia, extras nao sao detectados.
- Pula:
  - dias futuros
  - dias nao-uteis do sistema
  - dias com ausencia justificada (FERIAS, ABONO, FOLGA, FERIADO)
  - ultimas N horas (`DELAY_HOURS_AFTER_LAST = 24`) na rotina normal
- Em `/forcar` (dia ou range) que inclua hoje, aplica carencia de `FORCE_TODAY_GRACE_HOURS = 4`: so cobra marcacoes esperadas ate `agora - 4h`. Evita falso positivo enquanto o usuario ainda tem tempo de bater.
- Sistemas com escala variavel (ex: MentorH plantao 12x36) podem fornecer `expected` por dia via dispatcher (`baixar_dias`); o orquestrador respeita esse override antes do default do sistema. Permite detectar plantao noturno (entrada 19h dia X, saida 07h dia X+1) sem falso positivo.
- Quando um dia tem 0 marcacoes mas a folha indica motivo (ATESTADO, ABONO, FERIAS, FOLGA, FERIADO, DSR, FALTA), o dia e marcado como ausencia justificada (`is_workday=False`) e nao gera pendencia. Vale para os 3 sistemas (Senior tambem captura o texto da ocorrencia na propria linha do PDF). Quando o motivo nao casa com token conhecido, o dia segue como pendencia mas a mensagem inclui `_Motivo na folha:_` com o texto bruto, para o usuario decidir.

### 4.3 Notificacao

- Telegram (HTML parse mode) - canal interativo
- CallMeBot WhatsApp - apenas envio do cron
- Cabecalho com identificador do sistema (`SANTI` / `Sesa`)
- Quando 0 pendencias: mensagem amigavel "Ponto OK"

### 4.4 Chatbot Telegram

Comandos: `/checar`, `/forcar <data|range>`, `/status`, `/resetar`, `/ajuda`.

Formatos de data flexiveis: `DD/MM`, `DDMM`, `DD-MM`, `DDMMAAAA`, range com dash. Aceita cross-month.

Feedback de progresso via markers (`[cache]`, `[download]`, `[login]`, `[3/3 EXEREL]`, `[parse]`) que o bot transforma em mensagens humanas atualizadas durante a execucao.

Erros do sistema de ponto sao traduzidos em mensagens amigaveis por `friendly_error()` (HTTP 5xx -> "indisponivel", 401/403 -> "credenciais", timeout -> "demorou demais", DNS/SSL/conexao recusada, login invalido). Traceback Python nunca e exposto ao usuario.

### 4.5 Cron na VM

- `sex 15h`: roda `--system st`
- `sex 21h`: roda `--system ss`
- `seg 06h`: roda `--system eb` (cobre plantao de domingo + extras da semana)
- `diario 09h --system st`: justificativa Santi/Senior (manual no RH, exige presenca). Dispara em dia 8 e 21 do mes; se a data cair em sex/sab/dom, antecipa pra quinta anterior E adia pra segunda seguinte (2 notificacoes). Range: mes corrente.
- `diario 16h --system ss` e `--system eb`: justificativa Sesa e MentorH (online, qualquer dia/hora vale). Dispara somente em dia 1 do mes, sem shift. Range: mes anterior fechado.
- Em todos os casos: `justificativa.py` chama `monitor_ponto.py --force-range`, respeita carencia de 4h, motivo na folha (atestado/abono/etc) e so envia se houver pendencia real.
- Notificacao dupla: Telegram + WhatsApp

### 4.6 Caca A1 (opcional)

- Polling a cada 5 min na Oracle por capacidade ARM `VM.Standard.A1.Flex` (4 OCPU + 24 GB)
- Notifica Telegram somente em sucesso ou erro inesperado (nao a cada tentativa)
- Pode usar bot Telegram proprio via `CACA_A1_TELEGRAM_BOT_TOKEN` e `CACA_A1_TELEGRAM_CHAT_ID`; se ausentes, usa o bot principal.
- Auto-migra arquivos + systemd + cron pra nova VM
- Auto-desabilita servico apos sucesso (`systemctl disable caca-a1`)

## 5. Requisitos nao-funcionais

| Categoria       | Requisito |
|-----------------|-----------|
| Custo           | Zero. Apenas recursos Always Free da Oracle. |
| Disponibilidade | Bot deve sobreviver a reboot (Restart=on-failure). |
| Performance     | Cache de PDF (10 min) para evitar redownloads em comandos sequenciais. |
| Privacidade     | `.env` fora do git. Sessoes persistentes apenas locais (`active-sessions/`). |
| Observabilidade | Logs em `bot.log` e `caca_a1.log` na VM. |

## 6. Fora de escopo

- Bater ponto automaticamente (apenas leitura)
- Frontend web
- Multi-usuario
- Integracao com calendario / RH formal
- Migracao de outros workloads para a VM

## 7. Metricas de sucesso

- 0 pendencias nao detectadas
- 0 falsos positivos em dias com ausencia justificada
- Latencia do `/checar` < 30s com cache, < 90s sem
- Custo mensal Oracle = US$ 0,00
