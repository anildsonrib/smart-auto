"""Converte a auth key MTProto do Telegram Web (webk localStorage) numa
StringSession do Telethon, sem precisar de OTP. Testa lendo o chat do caca-a1."""
import json, asyncio, sys
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.crypto import AuthKey
from dotenv import load_dotenv
import os

# raiz do projeto (este script vive em tools/); .env e segredos ficam na raiz
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
API_ID   = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]

# IPs de producao dos data centers do Telegram
DC_IPS = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}

with open(os.path.join(ROOT, ".tg_authkey.json"), encoding="ascii") as f:
    data = json.load(f)

dc_id  = int(data["dcId"])
hexkey = data["authKey"]
auth_key_bytes = bytes.fromhex(hexkey)
assert len(auth_key_bytes) == 256, f"auth key inesperada: {len(auth_key_bytes)} bytes"

session = StringSession()
session.set_dc(dc_id, DC_IPS[dc_id], 443)
session.auth_key = AuthKey(auth_key_bytes)

async def main():
    client = TelegramClient(session, API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print("FALHA: auth key nao autorizou. Necessario login OTP.", file=sys.stderr)
        await client.disconnect()
        sys.exit(2)

    me = await client.get_me()
    print(f"OK autenticado como: {me.first_name} (id={me.id}, phone={me.phone})")

    # Salva a session string para reuso
    sess_str = client.session.save()
    with open(os.path.join(ROOT, ".tg_session.txt"), "w", encoding="ascii") as f:
        f.write(sess_str)
    print("SESSION_SAVED")

    # Le ultimas mensagens do bot do caca-a1
    msgs = await client.get_messages("@santi_caca_a1_alert_bot", limit=5)
    print("\n=== Ultimas mensagens caca-a1 ===")
    for m in reversed(msgs):
        print(f"[{m.date:%Y-%m-%d %H:%M:%S}] {m.text}")

    await client.disconnect()

asyncio.run(main())
