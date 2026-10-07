# SPEC - Correcoes de confiabilidade da checagem

Origem: incidente de 07/10/2026 em torno do dia 23/09/2026 (SANTI/Senior).
Metodo: spec-driven -> plano -> execucao com verificacao por etapa.

---

## 1. Contexto e evidencias

Em 23/09/2026 a folha Senior registrou **3 marcacoes** (07:03, 10:08, 13:07)
contra as 4 esperadas do horario 0260, e a propria Senior classificou o dia como
`Marcacoes Invalidas`. O dado segue identico na folha baixada em 07/10.

O que o sistema fez certo:
- Detectou a pendencia na checagem semanal e montou a mensagem
  (`*Quarta, 23/09* _Faltaram:_ 10:00`).
- Entregou no Telegram em 25/09 15:00 BRT, horario do `smart-auto-check@st.timer`.

O que falhou:
- O canal WhatsApp nao entregou (CallMeBot com conta pausada).
- Em 07/10 o usuario tentou 4x reavaliar o dia pelo bot e recebeu
  "Tudo certo. Nenhuma pendencia" nas 4 tentativas, porque `/checar` descarta
  argumentos de data em silencio.
- O diagnostico resultante foi incorreto (suspeita de inconsistencia no portal
  Senior), quando o portal e o backend sempre concordaram.

Nota lateral, ja esclarecida e fora do escopo desta spec: o RH consultou
23/06/2026 (terca, 4 marcacoes completas) em vez de 23/09/2026 (quarta).

---

## 2. Problemas

### P1 - `/checar` descarta argumentos em silencio

`telegram_bot.py`, handler de `/checar`:

```python
sys_key, _ = parse_args_sistema(args)   # o resto dos args e jogado fora
out = run_monitor(["--system", sys_key, "--no-state", "--no-whatsapp"], ...)
```

`/checar st 2309` roda a checagem do **mes corrente** e responde "Tudo certo",
sem nunca olhar 23/09.

- **Severidade**: alta. Falha silenciosa que devolve resposta positiva, gerando
  confianca falsa. Foi a causa do diagnostico errado.
- **Alcance**: `/checar`, e qualquer outro comando que use o mesmo descarte.

### P2 - Canal WhatsApp morto e invisivel

CallMeBot responde HTTP 208 com `Your Account is Paused due to technical issues`.
No log da VM: **0 envios com sucesso, 8 falhas**, desde ~19/08.

O fallback por Telegram funcionou em 8 de 8, entao nenhuma notificacao se perdeu.
Porem o usuario nao e informado de que o canal primario esta fora - a informacao
so existe no log da VM.

- **Severidade**: media. Nao ha perda de notificacao hoje, mas ha um canal
  degradado em silencio e um ponto unico de falha nao declarado.
- **Resolucao (07/10/2026)**: o usuario informou que o WhatsApp foi desligado de
  proposito, para notificar somente por Telegram. `WHATSAPP_ENABLED=false` foi
  aplicado no `.env` local e da VM. Sem a chave, o aviso de canal degradado
  apareceria em toda notificacao e viraria ruido permanente.

### P3 - Janela da checagem nao cobre o ciclo do RH

`monitor_ponto.py`, definicao de periodo na rotina normal:

```python
per_ini = today.replace(day=1)
per_fim = today.replace(day=monthrange(today.year, today.month)[1])
```

A competencia do RH no sistema `st` vai de **21 do mes anterior a 20 do corrente**
(confirmado no cabecalho da folha: `Periodo: 21/09/2026 a 20/10/2026`). A partir
de 01/10 a checagem automatica deixou de enxergar 23/09, embora o ciclo so feche
em 20/10 e o dia ainda fosse corrigivel.

- **Severidade**: alta. Pendencia real sai do radar enquanto ainda ha prazo.
- **Alcance**: `st`. `ss` e `eb` usam competencia mensal civil e nao sao afetados.

---

## 3. Requisitos e criterios de aceite

Cada requisito tem criterio verificavel por execucao, nao por inspecao visual.

### R1 - Nenhum comando do bot pode descartar argumentos em silencio

- **R1.1** `/checar st 2309` NAO deve responder "Tudo certo". Deve responder erro
  explicativo apontando o comando correto, citando a data reconhecida.
- **R1.2** A mensagem de erro deve conter a forma pronta para copiar
  (`/forcar 23/09 st`).
- **R1.3** `/checar st` (sem data) deve continuar funcionando exatamente como hoje.
- **R1.4** A mesma protecao vale para `/status` e `/resetar`.
- **R1.5** Argumento irreconhecivel (ex.: `/checar st abc`) tambem deve dar erro,
  nao silencio.

### R2 - Degradacao do canal de notificacao deve ser visivel ao usuario

- **R2.1** Quando o WhatsApp falhar e o Telegram assumir, a mensagem entregue no
  Telegram deve conter aviso de que o canal primario falhou, com o motivo curto.
- **R2.2** O aviso nao pode poluir a mensagem quando o WhatsApp funciona.
- **R2.3** Deve existir chave de configuracao para desligar o WhatsApp de vez
  (`WHATSAPP_ENABLED=false` no `.env`), sem editar codigo, suprimindo tentativa
  e aviso.
- **R2.4** Desligar o WhatsApp nao pode impedir a notificacao por Telegram.

### R3 - A janela da checagem deve acompanhar o ciclo de competencia do sistema

- **R3.1** `st` deve avaliar o ciclo aberto do RH (dia 21 do mes anterior ao dia
  20 do corrente quando hoje <= 20; dia 21 do corrente ao dia 20 do seguinte
  quando hoje > 20).
- **R3.2** Em 07/10/2026 a checagem normal de `st` deve enxergar 23/09 e acusar
  a pendencia.
- **R3.3** `ss` e `eb` devem manter a competencia mensal civil, sem alteracao de
  comportamento.
- **R3.4** O ciclo deve ser configuravel por sistema no dict `SYSTEMS`, nao
  hardcoded no fluxo.
- **R3.5** Datas futuras continuam ignoradas (regra pre-existente preservada).

### R4 - Nao regressao

- **R4.1** Os 3 sistemas continuam executando sem erro.
- **R4.2** Regras pre-existentes preservadas: carencia de 4h, `DELAY_HOURS_AFTER_LAST`,
  deteccao de motivo na folha, catch-up da justificativa, feriados, intervalo
  minimo entre marcacoes.
- **R4.3** Nenhum modulo ativo quebra import/compilacao.

---

## 4. Fora de escopo

- Reativar a conta CallMeBot (acao no provedor, do usuario).
- Corrigir a marcacao de 23/09 junto ao RH (acao humana, com prazo em 20/10).
- Alterar `ss`/`eb` para ciclo nao-civil.
- Versionamento/release do projeto.

---

## 5. Plano de execucao

Ordem escolhida por risco: P1 primeiro (e o que engana o usuario), P3 depois
(muda escopo de avaliacao), P2 por ultimo (menor risco funcional).

| Etapa | Acao | Verificacao de saida |
|-------|------|----------------------|
| E0 | Baseline: registrar comportamento atual dos 3 pontos | Saidas capturadas antes de qualquer edicao |
| E1 | R1 - rejeitar args nao consumidos em `/checar`, `/status`, `/resetar` | Simular os 4 comandos de 07/10 e exigir erro, nao "Tudo certo" |
| E2 | R3 - ciclo por sistema (`ciclo: "mes" \| "rh20"`) | Checagem normal de `st` em 07/10 acusa 23/09; `ss`/`eb` inalterados |
| E3 | R2 - aviso de canal degradado + `WHATSAPP_ENABLED` | Simular falha do WhatsApp e conferir aviso no texto do Telegram |
| E4 | Regressao R4 nos 3 sistemas | Execucao real de `st`, `ss`, `eb` sem erro |
| E5 | Deploy na VM + reinicio do bot + verificacao pos-deploy | Paridade de hash repo/VM, bot ativo, timers intactos |
| E6 | Teste ponta a ponta pelo bot real | `/checar st 2309` responde erro; `/forcar 2309 st` acusa a pendencia |

---

## 6. Riscos

| Risco | Mitigacao |
|-------|-----------|
| Mudar a janela de `st` faz re-notificar dias antigos do ciclo | Checagem agendada roda com `--no-state`; o dedup semanal por `ok_<semana>` e o `DELAY_HOURS_AFTER_LAST` seguem valendo. Validar na E2 que so 23/09 aparece |
| `monitor_ponto.py` ja tem trabalho nao commitado (intervalo minimo + feriados) | Nao commitar sem autorizacao; listar o delta ao final |
| Erro em `/checar` quebrar uso legitimo sem data | R1.3 cobre: teste explicito de `/checar st` puro |
| Deploy derrubar o bot | E5 verifica `is_active` e timers apos reinicio |

---

## 7. Verificacao final (definition of done)

1. `/checar st 2309` -> erro explicativo com `/forcar 23/09 st`.
2. `/checar st` -> funciona como antes.
3. Checagem normal de `st` em 07/10 -> acusa 23/09.
4. `ss` e `eb` -> comportamento identico ao baseline.
5. Falha simulada de WhatsApp -> aviso visivel na mensagem do Telegram.
6. `WHATSAPP_ENABLED=false` -> nenhuma tentativa, nenhum aviso, Telegram entrega.
7. Os 3 sistemas executam sem erro na VM.
8. Bot ativo, 6 timers, 0 units falhadas, hashes repo == VM.

---

## 8. Registro de execucao (07/10/2026)

| Etapa | Resultado |
|-------|-----------|
| E0 | Baseline: `/checar` descartava `['2309']`; janela 01-31/10; `ss`/`eb` com 0 pendencias |
| E1 | `erro_args_extras()` em `telegram_bot.py`, aplicado a `/checar`, `/status`, `/resetar` |
| E2 | `periodo_sistema()` + chave `ciclo` em `SYSTEMS`; `st`="rh20", `ss`/`eb`="mes" |
| E3 | `_motivo_falha_whatsapp()` + aviso no fallback + `WHATSAPP_ENABLED` |
| E4 | 9 modulos compilam; `st`/`ss`/`eb` exit 0; regras pre-existentes intactas |
| E5 | Deploy, bot reiniciado, 6 timers, 0 falhas, hashes identicos |
| E6 | Testes reais pelo bot (ver abaixo) |

Criterios da secao 7, todos verificados:

1. `/checar st 2309` -> `⚠️ /checar nao aceita data. Ignorado: 2309` + `/forcar 23/09 st`. OK
2. `/checar st` -> executa normal. OK
3. Checagem normal de `st` em 07/10 -> acusa `*Quarta, 23/09* _Faltaram:_ 10:00`. OK
4. `ss`/`eb` -> 0 pendencias, identico ao baseline. OK
5. Falha simulada do WhatsApp -> `⚠️ WhatsApp nao entregue (conta CallMeBot pausada); enviado so aqui.` OK
6. `WHATSAPP_ENABLED=false` -> CallMeBot nao chamado, Telegram entrega, sem aviso. OK
7. 3 sistemas executam sem erro na VM. OK
8. Bot ativo, 6 timers, 0 falhadas, hashes repo == VM. OK

Limites de ciclo conferidos em 7 datas, incluindo viradas de ano:
`07/10 -> 21/09..20/10`, `20/10 -> 21/09..20/10`, `21/10 -> 21/10..20/11`,
`05/01 -> 21/12..20/01`, `25/12 -> 21/12..20/01`.

### Nota sobre a justificativa (levantada durante a execucao)

Checado na epoca: a config dizia dias 8 e 21, entao o timer de 07/10 registrou
`[skip] nada devido`. **Corrigido em seguida**: o usuario confirmou que a janela
real do RH e dia **7 e 20** (era assim originalmente, em `b38be29`). Com 7/20,
07/10 **era** dia de disparo e a justificativa foi executada, avaliando
**20/09 a 20/10** e notificando 23/09. Ou seja, a justificativa ja constituia uma segunda rede de
protecao para esse dia, independente da correcao P3: ela nunca usou mes civil,
sempre o ciclo do RH.

Os disparos de 08/09 e 21/09 avaliaram 20/08 a 20/09 e corretamente nao citaram
23/09 - a anomalia e posterior ao corte de 20/09.
