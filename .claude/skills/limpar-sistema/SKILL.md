---
name: limpar-sistema
description: Remove artefatos efemeros do projeto smart_auto (PDFs baixados, screenshots adb, cache de downloaders, __pycache__, logs locais). Use quando o usuario pedir "faca a limpeza", "limpa o projeto" ou antes de commit/push para evitar sujar o repo. Nao toca em .env, monitor_state.json nem em codigo.
---

# Limpeza do smart_auto

## O que apagar (todos ja estao no .gitignore)

| Padrao                         | Origem                                  |
|--------------------------------|-----------------------------------------|
| `folha_ponto_*.pdf`            | cache de PDF Senior                     |
| `folha_ponto_*.html`           | cache HTML legado                       |
| `cache/`                       | JSON cache (st/ss/eb), TTL 600s         |
| `tg*.png`                      | screenshots do celular via adb          |
| `*.pyc`, `__pycache__/`        | bytecode Python                         |
| `*.log`                        | bot.log local                           |
| `tmp/`                         | temporarios                             |
| `captures/`, `midia/`, `waapi_apk/` | artefatos antigos de exploração     |

## O que NAO apagar

- `.env`, `.env.*` — segredos.
- `monitor_state.json` — estado runtime (ja_notificados); apagar zera o historico de notificacoes.
- `active-sessions/` — sessoes persistentes de browser (configuracao manual).
- `*.py`, `*.md`, `*.service`, `*.yaml` — codigo e config versionados.
- `.git/`, `.claude/` — sempre preservar.

## Comando (PowerShell, raiz do projeto)

```powershell
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue `
  folha_ponto_*.pdf, folha_ponto_*.html, `
  cache, tmp, captures, midia, waapi_apk, `
  tg*.png, oracle_*.png, oracle_*.html, oracle_snap.png, oracle_dump.html, `
  *.log, *.pyc

Get-ChildItem -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force
```

## Verificacao

```powershell
git status --short            # so deve mostrar arquivos versionados modificados (se houver)
git clean -nd                 # dry-run: lista o que ainda esta untracked
```

`git clean -nd` nao deve listar nenhum dos padroes acima — se listar, ampliar o comando de remocao.

## Quando aplicar tambem na VM

Se a sessao envolveu deploy/testes na VM (`smartauto`), limpar la tambem:

```bash
ssh smartauto "cd /home/ubuntu/smart_auto && \
  rm -f folha_ponto_*.pdf folha_ponto_*.html tg*.png && \
  rm -rf cache tmp __pycache__ && \
  find . -name '*.pyc' -delete"
```

Nao mexer em `bot.log` da VM sem o usuario pedir — ele e util para troubleshooting do systemd.

## Ordem recomendada apos limpeza

1. `git status` — confirmar limpo.
2. Se houver mudancas reais de codigo/doc, sugerir commit (perguntar antes — nunca commitar automaticamente).
3. Avisar o que foi removido em uma linha.
