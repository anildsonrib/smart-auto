---
name: telegram-adb-test
description: Enviar comandos pro bot do Telegram (@santi_smartauto_bot) usando o celular Android conectado via adb. Use sempre que precisar testar `/checar`, `/forcar`, `/status`, etc no fluxo real (bot na VM Oracle). Evita re-descobrir coordenadas, atalhos e armadilhas a cada sessao.
---

# Telegram via adb (celular conectado)

Objetivo: enviar uma mensagem ao chat do bot `Notificacao de Ponto` e capturar a resposta, com o minimo de cliques manuais.

## Pre-requisitos fixos

- Celular Samsung (Android One UI) conectado via USB, debug ativado.
- Resolucao fisica: **1080x2400** (`adb shell wm size`). Coordenadas abaixo assumem essa resolucao.
- App: `org.telegram.messenger`. Chat alvo: `Notificacao de Ponto` (bot `@santi_smartauto_bot`).
- Bot rodando na VM Oracle (`smartauto` host SSH). Mudancas em `monitor_ponto.py` exigem deploy antes do teste:
  ```
  scp monitor_ponto.py smartauto:/home/ubuntu/smart_auto/monitor_ponto.py && \
    ssh smartauto "sudo systemctl restart smart-auto-bot"
  ```

## Sempre usar PowerShell, nunca git-bash

Git-bash converte `/` em path Windows (`/forcar` -> `C:/Program Files/Git//forcar`). PowerShell (ou `cmd`) nao tem esse problema.

```powershell
adb shell input text "/forcar%s1404%sst"   # %s = espaco
```

`%s` no `input text` representa espaco. Nao use `" "` literal (truncaria no primeiro espaco).

## Fluxo end-to-end (caminho ja validado)

1. **Confirmar device**
   ```powershell
   adb devices
   ```
   Deve listar `RQCW40A351B device` (ou similar) - se aparecer `unauthorized` ou nada, pedir ao usuario pra reconectar.

2. **Abrir Telegram + ir pro chat do bot**

   O lancador as vezes nao abre direto. Sequencia robusta:
   ```powershell
   adb shell monkey -p org.telegram.messenger -c android.intent.category.LAUNCHER 1
   Start-Sleep -Seconds 2
   adb exec-out screencap -p > tg.png
   ```
   Tirar screenshot, ler com a tool Read e identificar em que tela esta:
   - **Lista de chats**: primeira linha e o bot `Notificacao de Ponto`. Tocar nela (aprox `540 230`).
   - **Tela de perfil do bot** (avatar grande + botoes Mensagem/Silenciar/Compartilhar/Parar): tocar em `Mensagem` (aprox `155 820`).
   - **Outra tela qualquer (home, Contatos, Spotify, etc)**: usar `adb shell input keyevent 4` (back) para sair ate cair em lista de chats. Se nada funcionar, repetir o passo 2.

3. **Verificar que esta no chat do bot**

   Cabecalho `Notificacao de Ponto / bot` no topo + caixa `Message` na base. Se o usuario tem um Reply pendente, cancelar com tap no `X` no chip de reply (evita tap acidental que abre reply em mensagem anterior - foi o caso de hoje).

4. **Tocar no campo de texto**
   ```powershell
   adb shell input tap 540 2240
   ```
   (centro do campo Message; coordenadas absolutas em 1080x2400)

5. **Digitar comando**
   ```powershell
   adb shell input text "/forcar%s1404%sst"
   ```

6. **Tocar no botao enviar** (icone paper plane azul)
   ```powershell
   adb shell input tap 1000 2220
   ```

   Importante: `keyevent 66` (ENTER) **nao envia** no Telegram - so insere quebra de linha. Sempre tap no botao.

7. **Aguardar resposta + screenshot**

   Comandos com download/login demoram 25-90s. Use Bash com `run_in_background: true`:
   ```bash
   sleep 25 ; adb exec-out screencap -p > /c/Users/<usuario>/Documents/smart_auto/tg_final.png
   ```
   Depois `Read` a imagem. Bot stream-a markers (Autenticando -> Baixando -> Avaliando -> resultado).

## Armadilhas conhecidas

- **MSYS path conversion**: nunca usar git-bash pra `adb shell input` com `/`. Use PowerShell.
- **Coordenadas display vs fisicas**: `input tap` usa coordenadas fisicas (1080x2400). Screenshots renderizados pela Read tool aparecem menores (~540 wide) - dobrar para obter coordenadas reais.
- **Reply chip ativo**: se o tap inicial caiu numa mensagem antiga, abre "Resposta a X" e o texto vai pro reply. Cancelar antes de digitar.
- **Spotify / Google login overlay**: as vezes aparece overlay de auth. Fechar com back ou tap em "Fechar tudo" antes de prosseguir.
- **Bot reiniciou**: se aparecer "Bot reiniciado." recente no chat, primeiro `/status st` para verificar que esta vivo antes do teste real.
- **sleep + chained command**: o harness bloqueia `sleep N ; cmd`. Use `run_in_background: true` ou Monitor com until-loop.
- **Send button x=1010 vs 1000**: a coluna do icone enviar varia ligeiramente entre versoes do Telegram. Se o primeiro tap nao enviou (texto continua na caixa), tentar `1000 2220` (mais centralizado no icone).

## Checklist rapido (copia-cola pro proximo teste)

```powershell
adb devices                                                            # 1. device ok?
adb shell monkey -p org.telegram.messenger -c android.intent.category.LAUNCHER 1
# screenshot + Read; navegar ate o chat
adb shell input tap 540 2240                                           # campo Message
adb shell input text "/forcar%s1404%sst"                               # comando
adb shell input tap 1000 2220                                          # enviar
# aguardar (Bash run_in_background com sleep + screencap), depois Read
```
