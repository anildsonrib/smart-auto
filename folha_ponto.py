"""
Baixa a folha de ponto do mes corrente (ou periodo informado) do Senior HCM Waapi/Rubi.

Fluxo:
  1. Login em platform.senior.com.br -> pega accessToken
  2. Chama webp18.seniorcloud.com.br:30121/rubiweb/conector com EXESENHA + redirectUrl pro HRCP101.APU
  3. Salva HTML/PDF da apuracao

Uso:
  python folha_ponto.py                          # mes corrente
  python folha_ponto.py 2026-04                  # mes especifico
  python folha_ponto.py 2026-04-01 2026-04-30    # periodo customizado
"""
import os
import sys
import re
import requests
from datetime import datetime, date
from calendar import monthrange
from pathlib import Path

ROOT = Path(__file__).parent
ENV = ROOT / ".env"

# --- Carrega .env ---
def load_env():
    env = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env

ENV_VARS = load_env()
USERNAME = ENV_VARS["WAAPI_USERNAME"]
PASSWORD = ENV_VARS["WAAPI_PASSWORD"]

# --- Configuracao especifica da empresa (descoberta na captura) ---
RUBI_HOST   = "https://webp18.seniorcloud.com.br:30121"
EMPRESA     = "4"        # dado_EAbrEmp
TIPO_CALC   = "1"        # dado_EAbrTcl
MATRICULA   = "400006"   # dado_EAbrCad
RELATORIO   = "HRCP101.APU"  # Apuracao de Ponto

PLATFORM_LOGIN = (
    "https://platform.senior.com.br/t/senior.com.br/bridge/1.0/rest/"
    "platform/authentication/actions/login"
)

# --- Helpers ---
def login_platform():
    """Login no Senior X e retorna o accessToken."""
    r = requests.post(
        PLATFORM_LOGIN,
        json={"username": USERNAME, "password": PASSWORD},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    import json as _json
    token = _json.loads(data["jsonToken"])
    print(f"[login] OK - accessToken: {token['access_token'][:8]}...")
    return token["access_token"]

def parse_periodo(args):
    """Retorna (dt_ini, dt_fim) no formato dd/mm/yyyy."""
    if len(args) == 0:
        hoje = date.today()
        ini = date(hoje.year, hoje.month, 1)
        fim = date(hoje.year, hoje.month, monthrange(hoje.year, hoje.month)[1])
    elif len(args) == 1 and re.fullmatch(r"\d{4}-\d{2}", args[0]):
        y, m = map(int, args[0].split("-"))
        ini = date(y, m, 1)
        fim = date(y, m, monthrange(y, m)[1])
    elif len(args) == 2:
        ini = datetime.strptime(args[0], "%Y-%m-%d").date()
        fim = datetime.strptime(args[1], "%Y-%m-%d").date()
    else:
        sys.exit("uso: folha_ponto.py [yyyy-mm | yyyy-mm-dd yyyy-mm-dd]")
    return ini.strftime("%d/%m/%Y"), fim.strftime("%d/%m/%Y")

def baixar_folha(access_token, dt_ini, dt_fim):
    """Acessa o Conector e baixa o HTML/PDF do relatorio HRCP101.APU."""
    from html.parser import HTMLParser
    session = requests.Session()

    redirect_url = (
        f"{RUBI_HOST}/rubiweb/conector?"
        f"ACAO=ENTRANCEREL&SIS=HR&NOME={RELATORIO}"
        f"&dado_EAbrEmp={EMPRESA}&dado_EAbrTcl={TIPO_CALC}"
        f"&dado_EEDatIni={dt_ini}&dado_EAbrCad={MATRICULA}"
        f"&dado_EEDatFim={dt_fim}"
    )
    auth_url = (
        f"{RUBI_HOST}/rubiweb/conector?"
        f"ACAO=EXESENHA&SIS=HR"
        f"&NomUsu={USERNAME.split('@')[0]}"
        f"&accessToken={access_token}"
        f"&redirectUrl={requests.utils.quote(redirect_url, safe=':/?&=')}"
    )
    print(f"[1/2 EXESENHA] {auth_url[:100]}...")
    r1 = session.get(auth_url, allow_redirects=True, timeout=30)
    print(f"[1/2 EXESENHA] status={r1.status_code} bytes={len(r1.content)} cookies={list(session.cookies.keys())}")

    # Parsea o form auto-submit
    class FormParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.action = None
            self.method = "post"
            self.fields = {}
        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "form":
                self.action = a.get("action")
                self.method = a.get("method", "post").lower()
            elif tag == "input":
                if "name" in a:
                    self.fields[a["name"]] = a.get("value", "")
    fp = FormParser()
    fp.feed(r1.text)
    print(f"[2/2 ENTRANCEREL] method={fp.method.upper()} action={fp.action}")
    print(f"[2/2 ENTRANCEREL] {len(fp.fields)} campos: {list(fp.fields.keys())[:10]}")

    # Submete o form (ENTRANCEREL -> retorna a tela de parametros rhpparam.htm)
    r2 = session.request(fp.method, fp.action, data=fp.fields, allow_redirects=True, timeout=60)
    print(f"[2/3 ENTRANCEREL] status={r2.status_code} bytes={len(r2.content)}")

    # Parsea o form de parametros HRCP101 que aponta pra EXEREL
    fp2 = FormParser()
    fp2.feed(r2.text)
    if not fp2.action or "EXEREL" not in (fp2.action or ""):
        # Fallback: salva o que tem
        out = ROOT / f"folha_ponto_{dt_ini.replace('/','-')}_{dt_fim.replace('/','-')}.html"
        out.write_bytes(r2.content)
        print(f"[out] form EXEREL nao encontrado; salvei rhpparam em {out}")
        return out, r2

    # Sobrescreve datas com o periodo solicitado
    fp2.fields["dado_EDatInR"] = dt_ini
    fp2.fields["dado_EDatFiR"] = dt_fim
    # Default value for radio "order_Colaboradores" se nao veio
    if "order_Colaboradores" not in fp2.fields:
        fp2.fields["order_Colaboradores"] = "0"

    print(f"[3/3 EXEREL] action={fp2.action[:100]}...")
    print(f"[3/3 EXEREL] datas={dt_ini} -> {dt_fim}")
    r3 = session.request(fp2.method, fp2.action, data=fp2.fields, allow_redirects=True, timeout=120)
    print(f"[3/3 EXEREL] status={r3.status_code} bytes={len(r3.content)} type={r3.headers.get('content-type','-')}")

    # Salva
    ct = r3.headers.get("content-type", "")
    if "pdf" in ct:    ext = "pdf"
    elif "html" in ct: ext = "html"
    else:              ext = "bin"
    out = ROOT / f"folha_ponto_{dt_ini.replace('/','-')}_{dt_fim.replace('/','-')}.{ext}"
    out.write_bytes(r3.content)
    print(f"[out] salvo em: {out}")
    return out, r3

def main():
    dt_ini, dt_fim = parse_periodo(sys.argv[1:])
    print(f"=== Folha de ponto: {dt_ini} a {dt_fim} ===")
    token = login_platform()
    out, resp = baixar_folha(token, dt_ini, dt_fim)
    ct = resp.headers.get("content-type", "")
    print(f"\n--- Resumo ---")
    print(f"  arquivo: {out}")
    print(f"  tamanho: {len(resp.content)} bytes")
    print(f"  tipo:    {ct}")
    if "html" in ct:
        # Preview limitado a ASCII pra evitar erro no console cp1252
        txt = resp.text[:600].encode("ascii", errors="replace").decode("ascii")
        print(f"\n  preview:\n{txt}")

if __name__ == "__main__":
    main()
