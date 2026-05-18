"""
Bot Telegram - canal de comandos manuais.

Comandos:
  /checar              roda monitor_ponto.py normal
  /forcar YYYY-MM-DD   forca avaliar um dia (ignora regra de 24h e state)
  /status              mostra state atual
  /resetar             apaga o state (proxima checagem re-notifica tudo)
  /ajuda               lista comandos

Loop de polling simples via getUpdates (long-poll 25s).
Aceita comandos somente do TELEGRAM_CHAT_ID configurado no .env.
"""
import json
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests

ROOT = Path(__file__).parent
ENV = ROOT / ".env"
STATE = ROOT / "monitor_state.json"
MONITOR = ROOT / "monitor_ponto.py"
PYTHON = sys.executable

def load_env():
    env = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env

ENV_VARS = load_env()
TOKEN = ENV_VARS["TELEGRAM_BOT_TOKEN"]
ALLOWED_CHAT = int(ENV_VARS["TELEGRAM_CHAT_ID"])
API = f"https://api.telegram.org/bot{TOKEN}"

def parse_data(s: str) -> date:
    """Aceita DD/MM, DD-MM, DDMM, DD/MM/AAAA, DDMMAAAA, etc. Levanta ValueError."""
    raw = s.strip().replace("-", "/").replace(" ", "")
    if "/" not in raw and raw.isdigit():
        if len(raw) == 4:   raw = f"{raw[:2]}/{raw[2:]}"
        elif len(raw) == 6: raw = f"{raw[:2]}/{raw[2:4]}/{raw[4:]}"
        elif len(raw) == 8: raw = f"{raw[:2]}/{raw[2:4]}/{raw[4:]}"
        else: raise ValueError(s)
    partes = raw.split("/")
    if len(partes) == 2:
        return date(date.today().year, int(partes[1]), int(partes[0]))
    if len(partes) == 3:
        ano = int(partes[2])
        if ano < 100: ano += 2000
        return date(ano, int(partes[1]), int(partes[0]))
    raise ValueError(s)

def normalizar_tokens_data(tokens: list[str]) -> list[str]:
    """Junta tokens livres em 1 ou 2 datas (range). Mantem tokens com '/' intactos."""
    if not tokens: return []
    # Caso especial: tokens 'cruz', ex ["06", "05"] -> "0605" (1 data)
    # ou ["06","05","2026"] -> "06052026"; ou ["0605","2005"] -> range; etc.
    # Estrategia: junta tudo separado por '-' onde havia '-' explicito.
    raw = " ".join(tokens)
    # Detecta range explicito (qualquer separador A-B com hifen)
    if raw.count("-") >= 1:
        # Tenta dividir por "-" preservando o que vier antes e depois
        # Se ja tem "/" nao mexe (deixa parse_data trabalhar)
        if "/" in raw:
            for sep in ("-", ".."):
                if sep in raw:
                    parts = [p.strip() for p in raw.split(sep, 1)]
                    if len(parts) == 2: return parts
        # Sem "/", junta digitos antes e depois do "-"
        partes = raw.split("-")
        if len(partes) == 2:
            a = partes[0].replace(" ", "")
            b = partes[1].replace(" ", "")
            # So considera range se cada lado tiver >= 4 digitos (DDMM minimo)
            # caso contrario e data unica tipo "06-05" -> "06/05"
            if a.isdigit() and b.isdigit() and len(a) >= 4 and len(b) >= 4:
                return [a, b]
    # Sem hifen: avalia tokens
    if len(tokens) == 1:
        return [tokens[0]]
    # Multiplos tokens: se todos forem digitos curtos, concatena
    if all(t.replace("/", "").isdigit() for t in tokens):
        # 2 tokens com 4+ chars cada -> assume range
        if len(tokens) == 2 and all(len(t.replace("/", "")) >= 4 for t in tokens):
            return tokens
        # Caso contrario, concatena tudo em 1 data
        return ["".join(t.replace("/", "") for t in tokens)]
    return tokens

def md_to_html(text: str) -> str:
    """Conversao simples Markdown -> HTML pra Telegram (mais robusto que parse_mode=Markdown)."""
    import re, html
    # escapa HTML primeiro
    out = html.escape(text)
    # ```code``` (multi-linha)
    out = re.sub(r"```([^`]*?)```", lambda m: f"<pre>{m.group(1)}</pre>", out, flags=re.DOTALL)
    # `inline code`
    out = re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", out)
    # *bold* (nao captura ** ou em final/inicio de palavra grudado)
    out = re.sub(r"\*([^\*\n]+?)\*", r"*\1*", out)
    # _italic_
    out = re.sub(r"_([^_\n]+?)_", r"<i>\1</i>", out)
    return out

def send(chat_id: int, text: str):
    try:
        r = requests.post(f"{API}/sendMessage", json={
            "chat_id": chat_id, "text": md_to_html(text), "parse_mode": "HTML",
        }, timeout=15)
        if not r.ok:
            print(f"[send-fail] {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[send-err] {e}")

SYS_LABELS = {"st": "SANTI", "ss": "Sesa"}

def parse_args_sistema(raw_args: list[str]) -> tuple[str | None, list[str]]:
    """Extrai sufixo de sistema (st/ss) dos args; retorna (sys|None, resto)."""
    sys_key = None
    rest = []
    for a in raw_args:
        if a.lower() in SYS_LABELS:
            sys_key = a.lower()
        else:
            rest.append(a)
    return sys_key, rest

ERR_SUFIXO = (
    "⚠️ Sufixo de sistema obrigatorio.\n\n"
    "Use `st` (SANTI) ou `ss` (Sesa) ao final do comando.\n"
    "Exemplos:\n"
    "`/checar st`\n"
    "`/forcar 06/05 ss`\n"
    "`/status st`"
)

def run_monitor(args: list[str], chat_id: int | None = None) -> str:
    """Roda monitor_ponto.py. Streaming de marcos + retorna resumo amigavel."""
    MARCOS = {
        "[cache]":      "📂 Usando cache local",
        "[download]":   "⬇️ Baixando folha",
        "[login]":      "🔑 Autenticando",
        "[3/3 EXEREL]": "📥 PDF recebido",
        "[parse]":      "🔍 Avaliando marcacoes",
    }
    enviados = set()
    t0 = time.time()
    try:
        proc = subprocess.Popen(
            [PYTHON, str(MONITOR)] + args,
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        buf = []
        for line in proc.stdout:
            buf.append(line)
            if chat_id is not None:
                for prefix, label in MARCOS.items():
                    if prefix in line and prefix not in enviados:
                        send(chat_id, f"_{label}_")
                        enviados.add(prefix)
                        break
        proc.wait(timeout=180)
        return format_summary("".join(buf), time.time() - t0)
    except subprocess.TimeoutExpired:
        proc.kill()
        return "❌ Tempo esgotado (>180s)."
    except Exception as e:
        return f"❌ Erro: `{e}`"

def _sistema_da_saida(raw: str) -> str:
    import re as _re
    m = _re.search(r"\[system\]\s+\S+\s+\(([^)]+)\)", raw)
    return m.group(1) if m else "o sistema de ponto"

def friendly_error(raw: str) -> str:
    """Converte traceback do monitor em mensagem clara para o usuario."""
    import re as _re
    sistema = _sistema_da_saida(raw)
    low = raw.lower()

    # HTTP do servidor de ponto
    m = _re.search(r"(\d{3})\s+(server error|client error|.*for url:\s*([^\s'\"]+))", raw, _re.I)
    if "httperror" in low or " server error" in low:
        codigo = m.group(1) if m else "?"
        if codigo.startswith("5"):
            return (f"⚠️ *{sistema} indisponivel*\n\n"
                    f"O servidor respondeu HTTP {codigo} (erro interno).\n"
                    f"Tente de novo em alguns minutos.")
        if codigo in ("401", "403"):
            return (f"⚠️ *Acesso negado em {sistema}*\n\n"
                    f"Credenciais podem ter expirado (HTTP {codigo}).\n"
                    f"Verifique usuario/senha no .env.")
        if codigo == "404":
            return (f"⚠️ *URL nao encontrada em {sistema}* (HTTP 404).\n"
                    f"O endereco do sistema pode ter mudado.")
        return f"⚠️ *{sistema}* respondeu HTTP {codigo}. Tente mais tarde."

    # Rede / DNS / timeout
    if "connectionerror" in low or "connection refused" in low or "connection reset" in low:
        return (f"⚠️ *Sem conexao com {sistema}*\n\n"
                f"O servidor recusou a conexao ou esta fora do ar.")
    if "name or service not known" in low or "nodename nor servname" in low or "getaddrinfo failed" in low:
        return (f"⚠️ *DNS falhou para {sistema}*\n\n"
                f"Nao consegui resolver o endereco do servidor.")
    if "timeout" in low or "timed out" in low:
        return (f"⏱️ *{sistema} demorou demais para responder*\n\n"
                f"Servidor lento ou instavel. Tente novamente.")
    if "sslerror" in low or "certificate" in low:
        return (f"⚠️ *Erro de certificado SSL em {sistema}*.")

    # Login invalido (sem HTTP)
    if "login" in low and ("invalid" in low or "incorret" in low or "senha" in low):
        return (f"⚠️ *Login invalido em {sistema}*\n\nRevise usuario/senha no .env.")

    # PDF/parser
    if "pdfplumber" in low or "pdf" in low and "vazio" in low:
        return f"⚠️ Nao consegui ler o PDF da folha de {sistema}."

    # Fallback - tipo da exception se conseguir extrair
    m2 = _re.search(r"([A-Za-z_][\w.]*Error|Exception):\s*(.+)", raw)
    if m2:
        tipo, msg = m2.group(1), m2.group(2).strip()
        if len(msg) > 160: msg = msg[:160] + "..."
        return f"❌ *Falha em {sistema}*\n\n`{tipo}: {msg}`"
    return f"❌ *Falha em {sistema}*. Tente novamente em alguns minutos."

def format_summary(raw: str, elapsed: float) -> str:
    """Converte o stdout do monitor em mensagem amigavel."""
    rodape = f"\n\n_{elapsed:.1f}s_"

    # 1. Tem bloco "--- Mensagem ---"? Pega o conteudo dele
    if "--- Mensagem ---" in raw:
        partes = raw.split("--- Mensagem ---", 1)[1]
        # corta no proximo marcador [
        msg = partes.split("\n[", 1)[0].strip()
        return msg + rodape

    # 2. Nada novo / ja notificado
    lower = raw.lower()
    if "nada novo a notificar" in lower:
        import re as _re
        m = _re.search(r"\((\d+) pendencia", lower)
        n = int(m.group(1)) if m else 0
        if n == 0:
            return "✅ Tudo certo. Nenhuma pendencia." + rodape
        plural = "s" if n > 1 else ""
        return f"✅ Sem novidades.\n\n{n} pendencia{plural} ja conhecida{plural} (ja notificada{plural} antes)." + rodape

    # 3. Erro
    if "[erro]" in lower or "traceback" in lower or "exception" in lower:
        return friendly_error(raw) + rodape

    # 4. Fallback - bloco bruto truncado
    if len(raw) > 1500:
        raw = raw[:1500] + "\n..."
    return f"```\n{raw.strip()}\n```" + rodape

def handle_command(chat_id: int, text: str):
    parts = text.strip().split()
    if not parts: return
    cmd = parts[0].lower().split("@", 1)[0]  # /cmd@bot -> /cmd
    args = parts[1:]

    if cmd in ("/start", "/ajuda", "/help"):
        send(chat_id,
             "*Notificacao de Ponto*\n\n"
             "*Sistemas suportados*\n"
             "`st` - SANTI (Senior HCM) | _padrao_\n"
             "`ss` - Sesa (Secullum)\n\n"
             "*Comandos* _(sufixo `st` ou `ss` obrigatorio)_\n"
             "`/checar st` - checagem agora (respeita 24h + state)\n"
             "`/forcar DD/MM ss` - avalia um dia (ignora 24h e state)\n"
             "`/forcar DD/MM-DD/MM ss` - avalia um range\n"
             "`/status st` - lista dias ja notificados\n"
             "`/resetar ss` - apaga o state do sistema\n"
             "`/ajuda` - este menu\n\n"
             "*Auto*\n"
             "Cron roda toda sexta 15h, envia alerta no WhatsApp se houver pendencia.\n\n"
             "*Formatos de data*\n"
             "`DD/MM` (ano corrente), `DD/MM/AAAA`, `DD-MM-AAAA`")
        return

    if cmd == "/resetar":
        sys_key, _ = parse_args_sistema(args)
        if sys_key is None:
            send(chat_id, ERR_SUFIXO); return
        if not STATE.exists():
            send(chat_id, "_State vazio._")
            return
        try:
            all_state = json.loads(STATE.read_text(encoding="utf-8"))
            # Migra retro-compat
            if not any(k in all_state for k in SYS_LABELS):
                all_state = {"st": all_state}
            removed = all_state.pop(sys_key, {})
            STATE.write_text(json.dumps(all_state, indent=2), encoding="utf-8")
            label = SYS_LABELS[sys_key]
            if removed:
                backup = json.dumps(removed, indent=2)
                send(chat_id, f"_State `{label}` apagado._\n\nBackup:\n```\n{backup}\n```")
            else:
                send(chat_id, f"_State `{label}` ja estava vazio._")
        except Exception as e:
            send(chat_id, f"erro: {e}")
        return

    if cmd == "/checar":
        sys_key, _ = parse_args_sistema(args)
        if sys_key is None:
            send(chat_id, ERR_SUFIXO); return
        send(chat_id, f"_Rodando checagem de {SYS_LABELS[sys_key]}..._")
        out = run_monitor(["--system", sys_key, "--no-whatsapp"], chat_id=chat_id)
        send(chat_id, out)
        return

    if cmd == "/forcar":
        sys_key, date_args = parse_args_sistema(args)
        if sys_key is None:
            send(chat_id, ERR_SUFIXO); return
        if not date_args:
            send(chat_id, "Uso: `/forcar DD/MM [st|ss]`\nou `/forcar DD/MM-DD/MM [st|ss]`")
            return
        tokens = normalizar_tokens_data(date_args)
        try:
            datas = [parse_data(t) for t in tokens[:2]]
        except ValueError as e:
            send(chat_id, f"Data invalida: `{e}`\nUse `DD/MM` ou `DD/MM/AAAA`")
            return
        label = SYS_LABELS[sys_key]
        if len(datas) == 1:
            d = datas[0]
            send(chat_id, f"_Forcando avaliacao de {d.strftime('%d/%m/%Y')} ({label})..._")
            out = run_monitor(["--system", sys_key, "--force-day", d.isoformat(), "--no-whatsapp"], chat_id=chat_id)
        else:
            ini, fim = sorted(datas)
            n = (fim - ini).days + 1
            send(chat_id, f"_Avaliando range {ini.strftime('%d/%m')} - {fim.strftime('%d/%m')} ({n} dias, {label})..._")
            out = run_monitor(["--system", sys_key, "--force-range", ini.isoformat(), fim.isoformat(), "--no-whatsapp"], chat_id=chat_id)
        send(chat_id, out)
        return

    if cmd == "/status":
        sys_key, _ = parse_args_sistema(args)
        if sys_key is None:
            send(chat_id, ERR_SUFIXO); return
        label = SYS_LABELS[sys_key]
        if not STATE.exists():
            send(chat_id, f"_State `{label}` vazio._")
            return
        try:
            all_state = json.loads(STATE.read_text(encoding="utf-8"))
            if not any(k in all_state for k in SYS_LABELS):
                all_state = {"st": all_state}
            data = all_state.get(sys_key, {})
            if not data:
                send(chat_id, f"_State `{label}` vazio._")
                return
            linhas = [f"`{k}` - {v}" for k, v in sorted(data.items())]
            send(chat_id, f"*State {label}:*\n" + "\n".join(linhas))
        except Exception as e:
            send(chat_id, f"erro lendo state: {e}")
        return

    send(chat_id, f"Comando desconhecido: `{cmd}`\nUse /ajuda")

COMMANDS = [
    ("checar",  "Checa pendencias. Use: /checar st|ss"),
    ("forcar",  "Avalia 1 dia ou range. Ex: /forcar 06/05 ss"),
    ("status",  "Lista dias notificados. Use: /status st|ss"),
    ("resetar", "Apaga state. Use: /resetar st|ss"),
    ("ajuda",   "Lista de comandos"),
]

def registrar_comandos():
    """Publica a lista no Telegram (aparece no menu /)."""
    try:
        r = requests.post(f"{API}/setMyCommands", json={
            "commands": [{"command": c, "description": d} for c, d in COMMANDS],
        }, timeout=10)
        print(f"[setup] setMyCommands {r.status_code} {r.text[:120]}")
    except Exception as e:
        print(f"[setup-err] {e}")

def main():
    print(f"[bot] iniciado. chat autorizado: {ALLOWED_CHAT}")
    registrar_comandos()
    send(ALLOWED_CHAT, "_Bot reiniciado._")
    offset = None
    while True:
        try:
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            r = requests.get(f"{API}/getUpdates", params=params, timeout=35)
            data = r.json()
            if not data.get("ok"):
                print(f"[poll-fail] {data}")
                time.sleep(5)
                continue
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg: continue
                chat_id = msg["chat"]["id"]
                text = msg.get("text", "")
                if chat_id != ALLOWED_CHAT:
                    print(f"[ignored] chat {chat_id}: {text[:60]}")
                    continue
                print(f"[recv] {text[:80]}")
                if text.startswith("/"):
                    handle_command(chat_id, text)
        except requests.exceptions.RequestException as e:
            print(f"[net-err] {e}")
            time.sleep(5)
        except Exception as e:
            print(f"[err] {e}")
            time.sleep(2)

if __name__ == "__main__":
    main()
