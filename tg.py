"""Utilitario Telegram (acesso de usuario via Telethon).

Le mensagens e envia comandos a qualquer bot/chat de dentro do VSCode,
sem celular. Usa a session string salva em .tg_session.txt (gerada por
tg_session_from_web.py a partir da sessao do Telegram Web).

Uso:
  python tg.py read <alvo> [n]          le as ultimas n mensagens (default 5)
  python tg.py send <alvo> <comando>    envia comando e mostra a resposta
  python tg.py last <alvo>              mostra a ultima mensagem

<alvo>: @username, id numerico, ou apelido configurado em ALIASES.

Exemplos:
  python tg.py last caca
  python tg.py read smartauto 10
  python tg.py send smartauto "/status"
"""
import sys, os, asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from dotenv import load_dotenv

load_dotenv()

# saida UTF-8 (emojis) mesmo no console cp1252 do Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

API_ID   = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tg_session.txt")

# apelidos -> entidade real
ALIASES = {
    "caca":      "@santi_caca_a1_alert_bot",
    "caca-a1":   "@santi_caca_a1_alert_bot",
    "smartauto": "@santi_smartauto_bot",
    "bot":       "@santi_smartauto_bot",
}

def resolve(alvo: str):
    if alvo in ALIASES:
        return ALIASES[alvo]
    if alvo.lstrip("-").isdigit():
        return int(alvo)
    return alvo

def load_session() -> str:
    if not os.path.exists(SESSION_FILE):
        sys.exit("Sem .tg_session.txt. Rode: python tg_session_from_web.py")
    with open(SESSION_FILE, encoding="ascii") as f:
        return f.read().strip()

def fmt(m):
    txt = m.text or ("<midia>" if m.media else "")
    return f"[{m.date:%Y-%m-%d %H:%M:%S}] {txt}"

async def do_read(client, alvo, n):
    msgs = await client.get_messages(resolve(alvo), limit=n)
    for m in reversed(msgs):
        print(fmt(m))

async def do_send(client, alvo, comando):
    ent = resolve(alvo)
    sent = await client.send_message(ent, comando)
    print(f"-> enviado: {comando}")
    # espera resposta nova (ate ~15s)
    for _ in range(30):
        await asyncio.sleep(0.5)
        msgs = await client.get_messages(ent, limit=3)
        novas = [m for m in msgs if m.id > sent.id and not m.out]
        if novas:
            for m in reversed(novas):
                print(fmt(m))
            return
    print("(sem resposta em 15s)")

async def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    acao, alvo = sys.argv[1], sys.argv[2]
    client = TelegramClient(StringSession(load_session()), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        sys.exit("Session expirada. Rode: python tg_session_from_web.py")
    try:
        if acao == "read":
            n = int(sys.argv[3]) if len(sys.argv) > 3 else 5
            await do_read(client, alvo, n)
        elif acao == "last":
            await do_read(client, alvo, 1)
        elif acao == "send":
            if len(sys.argv) < 4:
                sys.exit('Falta o comando. Ex: python tg.py send smartauto "/status"')
            await do_send(client, alvo, sys.argv[3])
        else:
            sys.exit(f"Acao invalida: {acao}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
