"""
Responder leve do bot do caca-a1 (@santi_caca_a1_alert_bot).

Faz long-poll em getUpdates (o token do caca-a1 so e usado para enviar pelo
provisionar_a1.py, entao nao ha conflito de polling) e responde comandos de
saude para o chat autorizado.

Comandos:
  /saude  (alias /status, /health) -> estado do servico caca-a1 na VM
  /log [n]                         -> ultimas n linhas do caca_a1.log (default 15)
  /help   (alias /start)           -> lista comandos

Roda como systemd service caca-a1-bot na VM.
"""
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

LOG_FILE = Path("/home/ubuntu/smart_auto/caca_a1.log")
SERVICE  = "caca-a1"

ENV = Path(os.environ.get("SMART_AUTO_ENV", "/home/ubuntu/smart_auto/.env"))

def env(k):
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == k:
                return value.strip().strip("\"'")
    return os.environ.get(k, "")

def env_any(*keys):
    for k in keys:
        v = env(k)
        if v:
            return v
    return ""

TG_TOKEN = env_any("CACA_A1_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
TG_CHAT  = int(env_any("CACA_A1_TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID") or 0)
API = f"https://api.telegram.org/bot{TG_TOKEN}"

def send(chat_id, text):
    try:
        requests.post(f"{API}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                      timeout=15)
    except Exception as e:
        print(f"[send-err] {e}", flush=True)

def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20)
    return (r.stdout or "").strip()

def humano(segundos):
    s = int(segundos)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    partes = []
    if d: partes.append(f"{d}d")
    if h: partes.append(f"{h}h")
    if m and not d: partes.append(f"{m}min")
    if not partes: partes.append(f"{s}s")
    return " ".join(partes)

def saude():
    estado = sh(f"systemctl is-active {SERVICE}") or "desconhecido"
    enabled = sh(f"systemctl is-enabled {SERVICE}") or "?"

    # uptime do servico
    ts = sh(f"systemctl show {SERVICE} -p ActiveEnterTimestamp --value")
    uptime = "?"
    if ts and ts != "n/a":
        try:
            dt = datetime.strptime(ts.rsplit(" ", 1)[0], "%a %Y-%m-%d %H:%M:%S")
            # ActiveEnterTimestamp vem no fuso local da VM (America/Sao_Paulo)
            uptime = humano((datetime.now() - dt).total_seconds())
        except Exception:
            uptime = ts

    # parse do log: ultima tentativa e ultima linha util
    ult_tentativa = "?"
    ult_linha = "?"
    if LOG_FILE.exists():
        linhas = [l for l in LOG_FILE.read_text(errors="replace").splitlines() if l.strip()]
        for l in reversed(linhas):
            m = re.search(r"tentativa #(\d+)\s*\(([^)]+)\)", l)
            if m:
                ult_tentativa = f"#{m.group(1)} ({m.group(2)})"
                break
        # ultima linha que nao seja FutureWarning ruido
        for l in reversed(linhas):
            if "FutureWarning" in l or "warnings.warn" in l:
                continue
            ult_linha = l.strip()
            break

    icone = "✅" if estado == "active" else "❌"
    return (
        f"🩺 *Saude caca-a1*\n\n"
        f"{icone} Servico: `{estado}` (enabled: `{enabled}`)\n"
        f"⏱ No ar ha: `{uptime}`\n"
        f"🔁 Ultima tentativa: `{ult_tentativa}`\n"
        f"📄 Ultimo log:\n`{ult_linha[:300]}`"
    )

def ultimas_linhas(n=15):
    if not LOG_FILE.exists():
        return "log nao encontrado."
    linhas = [l for l in LOG_FILE.read_text(errors="replace").splitlines() if l.strip()
              and "FutureWarning" not in l and "warnings.warn" not in l]
    sel = linhas[-n:]
    return "```\n" + "\n".join(sel)[-3500:] + "\n```"

def handle(text, chat_id):
    cmd = text.strip().split()
    base = cmd[0].lower().lstrip("/").split("@")[0]
    if base in ("saude", "status", "health"):
        send(chat_id, saude())
    elif base == "log":
        n = int(cmd[1]) if len(cmd) > 1 and cmd[1].isdigit() else 15
        send(chat_id, ultimas_linhas(n))
    elif base in ("help", "start"):
        send(chat_id,
             "*Comandos caca-a1*\n"
             "`/saude` - estado do servico na VM\n"
             "`/log [n]` - ultimas n linhas do log (default 15)\n"
             "`/help` - esta ajuda")
    else:
        send(chat_id, "Comando desconhecido. Use `/saude`, `/log` ou `/help`.")

def main():
    if not TG_TOKEN or not TG_CHAT:
        raise SystemExit("CACA_A1_TELEGRAM_BOT_TOKEN/CHAT_ID nao configurados.")
    print(f"caca-a1-bot iniciado. chat autorizado={TG_CHAT}", flush=True)
    offset = None
    while True:
        try:
            params = {"timeout": 50}
            if offset is not None:
                params["offset"] = offset
            r = requests.get(f"{API}/getUpdates", params=params, timeout=60)
            data = r.json()
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")
                if chat_id != TG_CHAT:
                    continue  # ignora chats nao autorizados
                if text.startswith("/"):
                    handle(text, chat_id)
        except requests.exceptions.RequestException as e:
            print(f"[poll-err] {e}", flush=True)
            time.sleep(5)
        except Exception as e:
            print(f"[err] {type(e).__name__}: {e}", flush=True)
            time.sleep(5)

if __name__ == "__main__":
    main()
