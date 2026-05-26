---
name: vm-runtime-pruner
description: Use when slimming the Oracle VM deployment for smart_auto so the server keeps only runtime files required to run the ponto monitor, Telegram bot, justificativa wrapper, and A1 hunter, while docs, skills, tests, local artifacts, and other accessories remain local. Includes guardrails for secrets, state files, systemd services, safe remote pruning, and post-prune health validation.
---

# VM Runtime Pruner

Use esta skill para deixar a VM Oracle enxuta, mantendo no servidor apenas o necessario para rodar
o `smart_auto` em producao 24/7.

## Contexto fixo

- Projeto local: `C:\Users\<usuario>\Documents\smart_auto`
- VM atual: a instancia Oracle usada por `smart_auto`
- SSH: `ssh -i ~/.ssh/oracle_smart_auto ubuntu@<vm-ip>`
- Pasta na VM: `/home/ubuntu/smart_auto`
- Servicos:
  - `smart-auto-bot.service`
  - `caca-a1.service` quando o hunter estiver ativo
  - `caca-a1-bot.service` quando existir o bot de apoio
- Telegram principal: o bot configurado em `.env`
- Telegram do hunter A1: opcional via `CACA_A1_TELEGRAM_BOT_TOKEN` e `CACA_A1_TELEGRAM_CHAT_ID`

## Objetivo

Separar claramente:

- **Local completo:** docs, `.claude/` skills, testes, PRD, README, artefatos de pesquisa e
  ferramentas de desenvolvimento.
- **VM runtime:** somente codigo e arquivos necessarios para manter os servicos rodando,
  baixando folhas, enviando notificacoes e, quando habilitado, provisionando A1.

## Nunca remover da VM

- `.env` atual
- `.venv/`
- `monitor_ponto.py`
- `telegram_bot.py`
- `justificativa.py`
- `folha_ponto.py`
- `folha_secullum.py`
- `folha_mentorh.py`
- `provisionar_a1.py`
- `caca_a1_bot.py`
- `smart-auto-bot.service`
- `caca-a1.service`
- `caca-a1-bot.service`
- `monitor_state.json`
- `cache/`
- `*.log` relacionados a execucao da VM, se usados como runtime
- `.tg_session.txt` e `.tg_authkey.json` somente se a VM realmente depender deles para o bot atual

Se a VM nao usa algum desses arquivos, nao assumir que precisa remove-lo sem confirmar.

## Candidatos normais a remover da VM

Remover somente por lista fechada e apos dry-run:

- `.claude/`
- `docs/`
- `memory/`
- `tests/`
- `reports/`
- `dist/`
- caches: `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `__pycache__/`, `*.pyc`
- `README.md`, `docs/PRD.md`, `AGENTS.md`
- arquivos de testes e utilitarios locais que nao sao importados pela VM
- backups antigos de segredo: `.env.bak.*`
- artefatos temporarios de debug que nao sejam usados pelo runtime

Se houver duvida entre runtime e apoio local, preservar.

## Regras de seguranca

- Nunca imprimir conteudo de `.env`, `.env.bak.*`, tokens, chat_id, API hash, session strings ou
  chaves SSH.
- Remover `.env.bak.*` sem imprimir conteudo, pois pode conter segredo antigo.
- Nunca usar `rsync --delete` global para esse objetivo.
- Nunca remover `cache/` ou `monitor_state.json` se a VM estiver usando isso para deduplicacao
  ou persistencia operacional.
- Antes de qualquer prune remoto, validar o estado dos servicos.
- Primeiro rodar dry-run e conferir a lista.
- Se a lista contiver algo fora dos candidatos esperados, parar.
- Se houver duvida, preservar.

## Fluxo obrigatorio

1. Validar estado atual da VM:

```bash
ssh -i ~/.ssh/oracle_smart_auto ubuntu@203.0.113.10 \
  "cd /home/ubuntu/smart_auto && systemctl is-active smart-auto-bot caca-a1 caca-a1-bot 2>/dev/null || true"
```

2. Usar o script local de prune `deploy/prune_vm_runtime.sh` (dry-run e o modo padrao;
   sem argumento equivale a `--dry-run`):

```bash
bash deploy/prune_vm_runtime.sh --dry-run
```

3. Aplicar somente depois de revisar lista fechada:

```bash
bash deploy/prune_vm_runtime.sh --apply
```

4. Validar pos-prune:

```bash
ssh -i ~/.ssh/oracle_smart_auto ubuntu@203.0.113.10 \
  "cd /home/ubuntu/smart_auto && systemctl is-active smart-auto-bot caca-a1 caca-a1-bot 2>/dev/null || true"
```

5. Testar Telegram sem celular, se o bot e a CLI local estiverem disponiveis:

```powershell
cd C:\Users\<usuario>\Documents\smart_auto
python tools/tg.py send smartauto "/status"
python tools/tg.py read smartauto 5
```

## Resultado esperado

Responder com:

- dry-run revisado;
- itens removidos;
- itens preservados;
- servicos ativos;
- validacao de runtime OK;
- nenhuma pendencia ou pendencias objetivas.
