"""
Baixa a folha de ponto do Secullum Ponto 4 via HTTP puro (sem browser).

Fluxo:
  1. GET pgLogin.aspx -> captura VIEWSTATE
  2. POST com txtUsuario/txtSenha -> sessao logada (cookies ASP.NET)
  3. POST em pgCalculos.aspx preenchendo periodo -> recebe HTML com tabela
  4. Parsea a tabela: [{date, ent_sai[], normais, faltas, ...}]

Uso:
  python folha_secullum.py                       # mes corrente
  python folha_secullum.py 2026-05-01 2026-05-31 # periodo customizado
"""
import json
import re
import sys
import time
import requests
from datetime import datetime, date
from calendar import monthrange
from pathlib import Path

ROOT = Path(__file__).parent
ENV = ROOT / ".env"

def load_env():
    env = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        env[k.strip()] = v
    return env

ENV_VARS = load_env()
BASE_URL = ENV_VARS["SECULLUM_BASE_URL"].rstrip("/") + "/"
USERNAME = ENV_VARS["SECULLUM_USERNAME"]
PASSWORD = ENV_VARS["SECULLUM_PASSWORD"]
CACHE_DIR     = ROOT / "cache"
CACHE_SECONDS = 600  # 10 min: mesmo padrao do Senior/MentorH

LOGIN_URL    = BASE_URL + "Paginas/pgLogin.aspx"
CALCULOS_URL = BASE_URL + "Paginas/pgCalculos.aspx"

RE_INPUT_VAL = lambda name, html: (
    re.search(r'<input[^>]+name="' + re.escape(name) + r'"[^>]+value="([^"]*)"', html)
)

def _get_hidden(name, html):
    m = RE_INPUT_VAL(name, html)
    return m.group(1) if m else ""

def login() -> requests.Session:
    s = requests.Session()
    r = s.get(BASE_URL, allow_redirects=True, timeout=15)
    r.raise_for_status()
    data = {
        "__EVENTTARGET": "lnkLogin",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": _get_hidden("__VIEWSTATE", r.text),
        "__VIEWSTATEGENERATOR": _get_hidden("__VIEWSTATEGENERATOR", r.text),
        "cboModoLogin": "0",
        "txtUsuario": USERNAME,
        "txtSenha": PASSWORD,
    }
    r2 = s.post(LOGIN_URL, data=data, allow_redirects=True, timeout=15)
    r2.raise_for_status()
    if "lnkLogin" in r2.text or "pgLogin.aspx" in r2.url:
        raise RuntimeError("Login Secullum falhou - verificar credenciais")
    print(f"[login] OK - URL={r2.url[-30:]}")
    return s

def baixar_periodo(s: requests.Session, ini: date, fim: date) -> str:
    """Faz GET na pgCalculos + POST com periodo + retorna HTML completo da pagina."""
    # GET na pagina pra capturar VIEWSTATE atualizado
    r = s.get(CALCULOS_URL, timeout=15)
    r.raise_for_status()
    # Postback no botao Atualizar com datas preenchidas
    data = {
        "ctl00$ScriptManager1": "",
        "__EVENTTARGET": "ctl00$ContentPlaceHolder1$lnkAtualizar",
        "__EVENTARGUMENT": "",
        "__LASTFOCUS": "",
        "__VIEWSTATE": _get_hidden("__VIEWSTATE", r.text),
        "__VIEWSTATEGENERATOR": _get_hidden("__VIEWSTATEGENERATOR", r.text),
        "ctl00$ContentPlaceHolder1$txtPeriodoIni": ini.strftime("%d/%m/%Y"),
        "ctl00$ContentPlaceHolder1$txtPeriodoFim": fim.strftime("%d/%m/%Y"),
        "ctl00$ContentPlaceHolder1$mskPeriodoIni_ClientState": "",
        "ctl00$ContentPlaceHolder1$mskPeriodoFim_ClientState": "",
        "ctl00$ContentPlaceHolder1$hidTipoData": "0",
    }
    # Mantem checkboxes conforme defaults (todos ligados)
    for chk in ("chkTOTAIS_RODAPE","chkTERMOS_MTB","chkLEGENDAS_JUSTIFICATIVAS",
                "chkLEGENDA_EVENTOS","chkMINIATURA_HORARIO","chkOBS"):
        data[f"ctl00$ContentPlaceHolder1${chk}"] = "on"
    r2 = s.post(CALCULOS_URL, data=data, timeout=30)
    r2.raise_for_status()
    return r2.text

# -----------------------------------------------------------------------
# PARSER da tabela renderizada
# -----------------------------------------------------------------------
RE_TR     = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
RE_CELULA = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
RE_TAGS   = re.compile(r"<[^>]+>")
RE_DATA   = re.compile(r"^(\d{2}/\d{2}/\d{2,4})\s*-\s*([a-zçãáéíóú]+)", re.IGNORECASE)

def _texto(html_fragment):
    return RE_TAGS.sub("", html_fragment).replace("&nbsp;", " ").strip()

def parse_html(html: str, ano_default: int):
    """Retorna list[{date, weekday, marcacoes[], normais, faltas, carga, raw[]}]."""
    dias = []
    for tr_m in RE_TR.finditer(html):
        celulas = [_texto(c) for c in RE_CELULA.findall(tr_m.group(1))]
        if not celulas: continue
        m = RE_DATA.match(celulas[0])
        if not m: continue
        ddmmaa, dia_sem = m.group(1), m.group(2)
        try:
            partes = ddmmaa.split("/")
            ano = int(partes[2])
            if ano < 100: ano += 2000
            d = date(ano, int(partes[1]), int(partes[0]))
        except Exception:
            continue
        # estrutura: Data | Ent.1 Sai.1 Ent.2 Sai.2 Ent.3 Sai.3 | Normais Faltas Ex... Carga
        dados = celulas[1:]
        marc = [c for c in dados[:6] if re.match(r"^\d{2}:\d{2}$", c)]
        normais  = dados[6]  if len(dados) > 6 else ""
        faltas   = dados[7]  if len(dados) > 7 else ""
        carga    = dados[-1] if dados else ""
        dias.append({
            "date":      d,
            "weekday":   dia_sem.lower()[:3],
            "marcacoes": marc,
            "normais":   normais,
            "faltas":    faltas,
            "carga":     carga,
            "raw":       celulas,
        })
    return dias

# -----------------------------------------------------------------------
def parse_periodo(args):
    if not args:
        hoje = date.today()
        ini = date(hoje.year, hoje.month, 1)
        fim = date(hoje.year, hoje.month, monthrange(hoje.year, hoje.month)[1])
    elif len(args) == 1 and re.fullmatch(r"\d{4}-\d{2}", args[0]):
        y,m = map(int, args[0].split("-"))
        ini = date(y, m, 1); fim = date(y, m, monthrange(y, m)[1])
    elif len(args) == 2:
        ini = datetime.strptime(args[0], "%Y-%m-%d").date()
        fim = datetime.strptime(args[1], "%Y-%m-%d").date()
    else:
        sys.exit("uso: folha_secullum.py [yyyy-mm | yyyy-mm-dd yyyy-mm-dd]")
    return ini, fim

def _cache_path(ini: date, fim: date) -> Path:
    return CACHE_DIR / f"secullum_{ini.isoformat()}_{fim.isoformat()}.json"

def _load_cache(ini: date, fim: date):
    p = _cache_path(ini, fim)
    if not p.exists(): return None
    age = time.time() - p.stat().st_mtime
    if age >= CACHE_SECONDS: return None
    data = json.loads(p.read_text(encoding="utf-8"))
    for d in data:
        d["date"] = date.fromisoformat(d["date"])
    print(f"[cache] reusando {p.name} (idade {int(age)}s)")
    return data

def _save_cache(ini: date, fim: date, dias: list[dict]):
    CACHE_DIR.mkdir(exist_ok=True)
    serial = [{**d, "date": d["date"].isoformat()} for d in dias]
    _cache_path(ini, fim).write_text(json.dumps(serial, ensure_ascii=False), encoding="utf-8")

def baixar(ini: date | None = None, fim: date | None = None):
    """API publica: retorna list[dict] dos dias parseados."""
    if ini is None or fim is None:
        ini, fim = parse_periodo([])
    cached = _load_cache(ini, fim)
    if cached is not None:
        return cached
    print(f"[download] baixando folha Secullum {ini} a {fim}")
    s = login()
    html = baixar_periodo(s, ini, fim)
    dias = parse_html(html, ini.year)
    _save_cache(ini, fim, dias)
    return dias

def main():
    ini, fim = parse_periodo(sys.argv[1:])
    print(f"=== Secullum: {ini} a {fim} ===")
    dias = baixar(ini, fim)
    print(f"{len(dias)} linhas extraidas")
    for d in dias:
        marc = "  ".join(d["marcacoes"]) or "(sem marc)"
        print(f"  {d['date']} {d['weekday']} | {marc:30} | normais={d['normais']:5} | faltas={d['faltas']:5}")

if __name__ == "__main__":
    main()
