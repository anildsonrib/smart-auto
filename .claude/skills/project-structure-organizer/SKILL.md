---
name: project-structure-organizer
description: Organiza este projeto smart_auto dentro do VS Code de forma segura, tecnica e incremental. Analisa arquivos soltos/desorganizados e propoe/implementa uma estrutura logica preservando funcionamento, imports, scripts, deploy VM, docs, memoria, estado operacional e historico de contexto. Use quando o usuario pedir "organize o projeto", "reestruture as pastas", "arrume as pastas", "separar arquivos", "limpar a raiz do projeto", "melhorar logistica interna" ou similar. Nunca move arquivos sem autorizacao explicita do usuario.
---

# project-structure-organizer

Reorganiza estrutura de pastas/arquivos do projeto `smart_auto`, preservando funcionamento, imports,
referencias, deploy na VM e historico. **Diagnostico antes**, aplicacao **so apos autorizacao
explicita**.

## Contexto deste projeto

- Projeto local: `C:\Users\<usuario>\Documents\smart_auto`
- Projeto Python com scripts soltos na raiz, skills locais em `.claude/skills/`, e fluxo de
  runtime centrado em `monitor_ponto.py`, `telegram_bot.py`, `justificativa.py` e
  os scripts auxiliares.
- O bot roda 24/7 na VM Oracle usando systemd, cron e arquivos de estado como
  `monitor_state.json`.
- `active-sessions/` pode conter atalhos de trabalho local e deve ser tratado com cuidado.
- O projeto pode nao estar em Git; se `git status` falhar, nao assumir comandos Git.

## Regras obrigatorias

1. Antes de mover, ler apenas o necessario para entender o projeto:
   - `pyproject.toml`, `requirements.txt`, `setup.py`, `setup.cfg`, `Pipfile`
   - `README*`, `CLAUDE.md`, `AGENTS.md`
   - `docs/PRD.md`, `docs/NEXT_PROMPT.md`, `docs/DECISIONS.md`, `memory/project_state.md` quando existirem
   - `*.service`, `*.timer`, `deploy/cloud-init.yaml`, scripts de deploy e bootstrap que existirem no repo
   - Arquivos de entrada (`main.py`, `app.py`, `index.ts`, `main.ts`, etc)
   - Listagem da arvore (top-level + 1 nivel).
2. Nunca mover as cegas: saber o que e cada arquivo antes de tocar.
3. Primeiro **diagnostico**, depois **proposta**, depois **execucao**.
4. Aplicar so com autorizacao explicita do usuario ("pode aplicar", "execute", "vai").
5. Preservar configuracao na raiz quando esperado por ferramentas:
   - `.gitignore`, `README.md`, `AGENTS.md`
   - `pyproject.toml`, `requirements.txt`, `setup.py`, `setup.cfg`, `Makefile`
   - `.mcp.json`, `.env` real e arquivos de session/telemetria quando a VM depender deles
   - `monitor_state.json`, caches e arquivos de runtime persistentes quando forem usados pelo sistema
6. **Nunca mover `.env` real** sem autorizacao.
7. **Nunca mover arquivos de session/credenciais** para "organizar"; no maximo documentar e manter
   fora do alcance de commits.
8. **Nunca apagar arquivos** sem autorizacao explicita.
9. Apos mover, **corrigir referencias** afetadas: imports, rotas, paths, aliases (`tsconfig` paths, `vite` resolve.alias), scripts em `package.json`, links em docs, paths em deploy/systemd, `sync_to_vm.sh`, testes e comandos Telegram.
10. Apos mover, **rodar validacao minima** do stack: `python -m ruff check .`, `python -m pytest -q`,
    e smoke quando afetar runtime (`python monitor_ponto.py --system st --dry` ou comando equivalente
    apropriado ao arquivo afetado).
11. Se a organizacao afetar codigo em uso na VM, sincronizar a VM com o fluxo local existente do
    repositório e validar os servicos relevantes.
12. Manter respostas compactas, tecnicas, objetivas. Sem arquitetura complexa fantasiosa. Minimo necessario. Priorizar baixo risco: pequenas mudancas, reversiveis, faceis de revisar.

## Convencoes de organizacao por tipo de projeto

### Web / Frontend (JS / TS — React, Next, Vue, Svelte, etc)
```
src/
  components/
  pages/ ou app/
  lib/
  services/
  hooks/
  styles/
assets/ ou public/
tests/
docs/
scripts/
```

### Python (FastAPI, Django, Flask, CLI, ML)
```
src/
tests/
scripts/
docs/
memory/
active-sessions/
deploy/
state/
```

### Estrutura esperada para este `smart_auto`

```
smart_auto/
  monitor_ponto.py        # orquestrador principal de ponto
  telegram_bot.py         # bot de Telegram do ponto
  justificativa.py        # wrapper de justificativa por sistema
  folha_ponto.py          # downloader Senior
  folha_secullum.py       # downloader Secullum
  folha_mentorh.py       # downloader MentorH
  *.service               # units systemd na raiz quando o deploy espera isso
  monitor_state.json      # estado de deduplicacao
  docs/                   # documentacao (ex.: docs/PRD.md)
  tools/                  # utilitarios locais (ex.: tools/tg.py)
  deploy/                 # bootstrap/prune (deploy/cloud-init.yaml, deploy/prune_vm_runtime.sh)
  .claude/skills/         # skills locais do workspace
  active-sessions/        # atalhos/sessoes locais
```

### Outros / mistos
- Scripts utilitarios soltos -> `scripts/`
- Documentacao solta -> `docs/`
- Imagens / videos / audios -> `assets/`, `media/` ou `public/`
- Outputs gerados -> `output/`
- Nao deixar artefatos temporarios na raiz.

## Fluxo obrigatorio (10 etapas)

**Etapa 1 — Inspecionar estrutura atual**
- Listar arvore top-level e segundo nivel.
- Identificar arquivos soltos na raiz.

**Etapa 2 — Identificar tipo do projeto**
- Stack (Node, Python, Go, etc), framework (Vite, Next, FastAPI, Django).
- Ferramentas, build system, gerenciador de pacotes.
- Pontos sensiveis (config, segredos, paths absolutos hardcoded).

**Etapa 3 — Listar problemas de organizacao**
- Arquivos soltos sem categoria, docs misturada com codigo, scripts ad-hoc na raiz, output versionado por engano, etc.

**Etapa 4 — Propor estrutura nova em arvore**
- Apresentar arvore alvo em bloco ```text```.
- Seguir as convencoes acima, adaptando ao stack identificado.

**Etapa 5 — Plano de movimentacao**
- Lista arquivo-por-arquivo OU por grupos coerentes.
- Formato: `<origem> -> <destino>`.
- Indicar tambem quais arquivos serao preservados na raiz por requisito de ferramenta/projeto.

**Etapa 6 — Aguardar autorizacao**
- **PARAR aqui**. Nao mover nada ate o usuario aprovar explicitamente.

**Etapa 7 — Aplicar mudancas**
- Usar `git mv <origem> <destino>` para arquivos **tracked** se o projeto for Git
  (preserva historico).
- Usar `Move-Item` (Windows) ou `mv` (Unix) para arquivos **untracked**.
- Criar pastas com `New-Item -ItemType Directory -Force <path>` (Windows) ou `mkdir -p <path>` (Unix) quando necessario.
- No Windows, antes de qualquer move recursivo, confirmar caminhos absolutos resolvidos dentro
  de `C:\Users\<usuario>\Documents\smart_auto`.

**Etapa 8 — Corrigir imports / paths / scripts / docs**
- `grep` nos arquivos por referencias aos caminhos antigos.
- Atualizar imports nos arquivos de codigo.
  - Atualizar scripts de automacao locais quando existirem.
  - Atualizar aliases e caminhos de configuracao quando aplicavel.
- Atualizar `deploy/`, systemd units, `sync_to_vm.sh`, `vm_deploy.sh`, docs e testes quando
  paths mudarem.
- Atualizar arvore em `README.md`, `STRUCTURE.md`, `AGENTS.md`, `CLAUDE.md`, `docs/NEXT_PROMPT.md`,
  `docs/DEPLOY_GUIDE.md`, etc. quando aplicavel.

**Etapa 9 — Validacao minima**
- Executar o comando de validacao do stack:
  - Python/smart_auto: `python -m ruff check .`, `python -m pytest -q`
  - Smoke test: executar o entrypoint apropriado ao arquivo alterado, por exemplo
    `python monitor_ponto.py --system st --dry` ou `python telegram_bot.py`
- Reportar saida resumida (ok / falha + linha do erro).

**Etapa 10 — Relatorio final obrigatorio**

```
Arquivos movidos:
- <origem> -> <destino>
- ...

Pastas criadas:
- <pasta>
- ...

Imports / scripts / docs ajustados:
- <arquivo>: <o que mudou>
- ...

Validacao executada:
- <comando>: <resultado>

Pendencias ou riscos restantes:
- <item ou "nenhum">
```

## Documentacao da nova estrutura (opcional)

Se o usuario pedir documentacao da nova organizacao:
- Criar `STRUCTURE.md` com a arvore + descricao curta de cada pasta, **ou**
- Atualizar a secao de estrutura no `README.md`.

Nao criar ambos se nao for solicitado.

## Integracao com CLAUDE.md / AGENTS.md

- Se existir `CLAUDE.md` (raiz ou global) ou `AGENTS.md`, ler e **obedecer suas regras de tom, formato e guardrails**.
- Se essas regras definirem um formato especifico de relatorio final, usar o desse arquivo no lugar do template padrao da Etapa 10.

## Formato de resposta **antes** de aplicar (etapas 1–6)

```
Diagnostico:
[resumo curto: tipo do projeto, principais problemas]

Estrutura proposta:
​```text
project/
  src/
  docs/
  scripts/
  tests/
​```

Plano de movimentacao:
- <origem> -> <destino>
- <origem> -> <destino>
- ...

Aguardando autorizacao para aplicar.
```

(Os caracteres `​` no template acima representam as 3 crases do bloco de codigo — usar 3 crases literais na saida real.)

## Guardrails

- Nao mover `.env`, `.env.local`, `.env.production`, etc sem autorizacao.
- Nao apagar arquivos sob nenhuma circunstancia sem autorizacao explicita.
- Nao mover arquivos de estado, backups, `.claude/skills/` ou arquivos de segredo/sessao sem
  avaliar o impacto no runtime.
- Nao inventar arquitetura — fazer o **minimo necessario** para resolver os problemas listados na Etapa 3.
- Mudancas devem ser reversiveis e faceis de revisar (preferir 1 commit por etapa logica).
- Em caso de duvida sobre o destino de um arquivo, **perguntar** antes de mover.
- Usar `git mv` em vez de `mv` quando o projeto for Git e o arquivo estiver tracked, para
  preservar historico via rename detection.
-- Se a mudanca for aplicada localmente e afetar runtime/deploy, concluir com recomendacao ou
  execucao do processo de sincronizacao apropriado ao repo.
