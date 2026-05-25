---
name: chrome-cdp-persistent-shortcut
description: Gera atalho do Google Chrome para Windows com sessao persistente (--user-data-dir) e Chrome DevTools Protocol habilitado (--remote-debugging-port). Use quando o usuario pedir "atalho do Chrome", "chrome com cdp", "chrome com debug port", "perfil persistente do chrome" ou similar — especialmente para automacao, scraping ou monitor de rede (CDP). Pergunta apenas a URL alvo se nao fornecida. Gera nome da pasta de perfil em PascalCase a partir do dominio. Atribui porta CDP sequencial a partir de 9234. Saida no formato fixo abaixo.
---

# chrome-cdp-persistent-shortcut

Cria (de fato, via PowerShell) um atalho `.lnk` do Chrome no Windows com:
- `--remote-debugging-port=<PORTA>`
- `--user-data-dir="C:\ChromePerfis\<NomePastaDebug>"`
- `--profile-directory="Default"`
- URL inicial

O atalho e salvo dentro do projeto, na pasta `active-sessions\` (criada se nao existir). O perfil persistente continua em `C:\ChromePerfis\` (fora do repo - e pesado e nao deve ser versionado).

## Entrada

Apenas **URL alvo**. Se nao fornecida, perguntar **so isso**.

## Validacao da URL

1. **Espacos** -> URL invalida. Responder `URL parece invalida (contem espacos). Corrija e tente novamente.` e parar.
2. **Sem protocolo** -> adicionar `https://`. Excecao: se o usuario informou `http://` explicitamente, manter.
3. **Query string e fragments** -> preservar exatamente como informado.

## Nome da pasta do perfil

Algoritmo:

1. Extrair o **host** (entre `://` e `/`, ou fim da string).
2. Remover prefixo `www.` se presente.
3. Remover TLDs comuns no fim: `.com`, `.com.br`, `.org`, `.org.br`, `.net`, `.io`, `.gov.br`, `.edu.br`, `.app`, `.dev`. Se o host nao termina com nenhum desses, manter todos os componentes.
4. Quebrar o que sobrou em tokens por `.`, `-`, `_`.
5. Para cada token, se for uma string contigua de palavras obvias em PT ou EN, dividir em palavras (`pontodobicho` -> `Ponto`, `Do`, `Bicho`). Se nao for obvio, manter como um unico token.
6. Capitalizar a primeira letra de cada token (PascalCase). Remover qualquer caractere fora de `[A-Za-z0-9]`.
7. Concatenar todos os tokens + sufixo `Debug`.

Exemplos:

| URL                                       | Pasta                          |
|-------------------------------------------|--------------------------------|
| `https://pontodobicho.com`                | `PontoDoBichoDebug`            |
| `https://www.exemplo.com.br/path?x=1`     | `ExemploDebug`                 |
| `https://aghu.hucam-ufes.ebserh/x`        | `AghuHucamUfesEbserhDebug`     |
| `http://localhost:8000`                   | `LocalhostDebug`               |
| `https://sub.api.exemplo.io`              | `SubApiExemploDebug`           |

## Porta CDP

Atribuir a partir de **9234**, sequencial.

- Porta 9233 ja esta reservada (usada por `monitor-scraping/aghu-persistente.lnk` neste repositorio).
- Varrer o contexto da conversa atual em busca de portas ja atribuidas por chamadas anteriores desta skill (9234, 9235, ...). Atribuir a proxima livre.
- Primeira invocacao na conversa: 9234.

## Caminho base do Chrome

```
"C:\Program Files\Google\Chrome\Application\chrome.exe"
```

## Criacao do atalho (OBRIGATORIO)

Criar o `.lnk` de fato, em `active-sessions\<NomePastaDebug>.lnk` na raiz do projeto. Rodar via PowerShell (a pasta `active-sessions` e criada se nao existir):

```powershell
$proj = (Get-Location).Path
$dir  = Join-Path $proj "active-sessions"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$lnk  = Join-Path $dir "<NomePastaDebug>.lnk"
$ws   = New-Object -ComObject WScript.Shell
$sc   = $ws.CreateShortcut($lnk)
$sc.TargetPath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$sc.Arguments  = '--remote-debugging-port=<PORTA> --user-data-dir="C:\ChromePerfis\<NomePastaDebug>" --profile-directory="Default" "<URL>"'
$sc.Save()
```

- Substituir `<PORTA>`, `<NomePastaDebug>` e `<URL>` pelos valores calculados.
- Se o `.lnk` ja existir, sobrescrever (CreateShortcut + Save sobrescreve).

## Formato da saida (OBRIGATORIO, sem texto extra antes ou depois)

```
Atalho criado: active-sessions\<NomePastaDebug>.lnk

Alvo: "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=<PORTA> --user-data-dir="C:\ChromePerfis\<NomePastaDebug>" --profile-directory="Default" "<URL>"

Pasta da sessao persistente: C:\ChromePerfis\<NomePastaDebug>

Endpoint CDP: http://localhost:<PORTA>/json
```

## Restricoes

- **Nao** explicar conceitos (`--user-data-dir`, CDP, etc.). So criar o atalho e dar a saida.
- **Nao** sugerir proximos passos depois da saida.
- **Sempre** criar o `.lnk` via PowerShell antes de imprimir a saida (nao apenas descrever).
- Resposta compacta. Nada alem do modelo acima.
