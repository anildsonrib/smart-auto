"""Downloader do espelho de ponto MentorH (SIGP EBSERH) - HTTP puro.

Fluxo:
1. GET /portal.csp -> 302 /index.csp (cookies de sessao)
2. Extrai WEVENT do submit handler do form de login
3. POST %CSP.Broker.cls com cpf+senha (login)
4. GET /portal.csp logado -> extrai WEVENT do "acessarMenu"
5. POST %CSP.Broker.cls com menu=100.3.6.1 -> extrai CSPToken do espelho
6. POST portalRegFrequencia5.csp com periodo (atualPrimData/atualUltData)
7. Parseia tabela HTML

Sem browser. Roda em qualquer maquina com Python + requests.
"""
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).parent
ENV  = ROOT / ".env"

def load_env():
    env = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'): v = v[1:-1]
        env[k.strip()] = v
    return env

ENV_VARS = load_env()
HOST   = "https://sigp.ebserh.gov.br"
BASE   = f"{HOST}/csp/ebserh/portal/novo"
BROKER = f"{BASE}/%CSP.Broker.cls"
USER   = ENV_VARS.get("MENTORH_USERNAME", "")
PWD    = ENV_VARS.get("MENTORH_PASSWORD", "")
MENU   = "100.3.6.1"  # Folha de Frequencia
EPOCH  = date(1840, 12, 31)  # Intersystems Cache $HOROLOG

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148"

_RE_LINHA = re.compile(r"^\s*(\d{1,2})\s+(SEG|TER|QUA|QUI|SEX|S[ÁA]B|DOM)\b", re.I)
_RE_HORA  = re.compile(r"(\d{2}:\d{2})")
_RE_CELL  = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S)
_RE_ROW   = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
_RE_TAGS  = re.compile(r"<[^>]+>")
_RE_WS    = re.compile(r"\s+")

def _to_horolog(d: date) -> int:
    return (d - EPOCH).days

def _clean(s: str) -> str:
    s = _RE_TAGS.sub(" ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    return _RE_WS.sub(" ", s).strip()

def _sess() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s

def _login(s: requests.Session):
    print("[mentorh] login...")
    r = s.get(f"{BASE}/portal.csp", timeout=20, allow_redirects=True)
    m = re.search(r"mountAjaxData\('([^']+)',\$\('#login'\)\.val\(\)", r.text)
    if not m: raise RuntimeError("[mentorh] WEVENT de login nao encontrado")
    wevent = m.group(1)
    r2 = s.post(BROKER, timeout=20, headers={
        "Referer": f"{BASE}/index.csp",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }, data={"WARGC":"3","WEVENT":wevent,"WARG_1":USER,"WARG_2":PWD,"WARG_3":""})
    if '"Tipo":"S"' not in r2.text:
        raise RuntimeError(f"[mentorh] login falhou: {r2.text[:300]}")

def _abrir_menu(s: requests.Session, codigo: str) -> str:
    """Retorna a URL absoluta do espelho com CSPToken."""
    r = s.get(f"{BASE}/portal.csp", timeout=20)
    # WEVENT do acessarMenu (chamada cspHttpServerMethod dentro da funcao)
    m = re.search(
        r"function\s+acessarMenu\s*\([^)]*\)\s*\{\s*cspHttpServerMethod\('([^']+)'",
        r.text,
    )
    if not m: raise RuntimeError("[mentorh] WEVENT de acessarMenu nao encontrado")
    wevent = m.group(1)
    r2 = s.post(BROKER, timeout=20, headers={
        "Referer": f"{BASE}/portal.csp",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }, data={"WARGC":"1","WEVENT":wevent,"WARG_1":codigo})
    m2 = re.search(r"\.debug-csp'\)\.val\('([^']+)'\)", r2.text)
    if not m2: raise RuntimeError(f"[mentorh] URL do menu nao encontrada na resposta: {r2.text[:300]}")
    rel = m2.group(1)  # /csp/.../portalRegFrequencia5.csp?CSPToken=...
    return HOST + rel

def _baixar_periodo(s: requests.Session, espelho_url: str, ini: date, fim: date) -> str:
    """GET inicial p/ pegar hidden fields, depois POST com periodo ajustado."""
    r = s.get(espelho_url, timeout=30, headers={
        "Referer": f"{BASE}/portal.csp",
    })
    # extrai TODOS os inputs hidden do form
    form = re.search(r"<form[^>]*>(.*?)</form>", r.text, re.I | re.S)
    if not form: raise RuntimeError("[mentorh] form nao encontrado na resposta")
    inputs = dict(re.findall(
        r"<input[^>]*name=[\"']([^\"']+)[\"'][^>]*value=[\"']([^\"']*)[\"']",
        form.group(1), re.I,
    ))
    # ajusta periodo
    inputs["atualPrimData"] = str(_to_horolog(ini))
    inputs["atualUltData"]  = str(_to_horolog(fim))
    # POST
    action = re.search(r"<form[^>]*action=[\"']([^\"']+)[\"']", r.text, re.I)
    post_url = action.group(1) if action else espelho_url.split("?")[0]
    if post_url.startswith("/"): post_url = HOST + post_url
    r2 = s.post(post_url, data=inputs, timeout=30, headers={
        "Referer": espelho_url,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    return r2.text

def _parse(html: str, ini: date, fim: date) -> list[dict]:
    out = []
    for row in _RE_ROW.findall(html):
        cells = [_clean(c) for c in _RE_CELL.findall(row)]
        if len(cells) < 4: continue
        m = _RE_LINHA.match(cells[0])
        if not m: continue
        dia = int(m.group(1))
        wk  = m.group(2).upper().replace("Á","A")[:3]
        d = None
        for cand_mes, cand_ano in ((ini.month, ini.year), (fim.month, fim.year)):
            try:
                cand = date(cand_ano, cand_mes, dia)
                if ini <= cand <= fim:
                    d = cand; break
            except ValueError:
                continue
        if d is None: continue
        hor_str = cells[2] if len(cells) > 2 else ""
        punches = _RE_HORA.findall(hor_str)
        out.append({
            "date":      d,
            "weekday":   wk,
            "marcacoes": punches,
            "raw":       cells,
        })
    return out

def baixar(ini: date, fim: date) -> list[dict]:
    print(f"[mentorh] periodo {ini.isoformat()} a {fim.isoformat()}")
    if not USER or not PWD:
        raise RuntimeError("MENTORH_USERNAME/MENTORH_PASSWORD nao configurados no .env")
    s = _sess()
    _login(s)
    print("[mentorh] abrindo menu Folha de Frequencia")
    espelho = _abrir_menu(s, MENU)
    print(f"[mentorh] baixando espelho")
    html = _baixar_periodo(s, espelho, ini, fim)
    dias = _parse(html, ini, fim)
    print(f"[mentorh] {len(dias)} linha(s) extraidas")
    return dias

if __name__ == "__main__":
    import json
    if len(sys.argv) >= 3:
        ini = date.fromisoformat(sys.argv[1])
        fim = date.fromisoformat(sys.argv[2])
    else:
        hoje = date.today()
        ini = hoje.replace(day=1)
        from calendar import monthrange
        fim = hoje.replace(day=monthrange(hoje.year, hoje.month)[1])
    res = baixar(ini, fim)
    print(json.dumps([{**r, "date": r["date"].isoformat()} for r in res], indent=2, ensure_ascii=False))
