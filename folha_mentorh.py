"""Downloader do espelho de ponto MentorH (SIGP EBSERH) - HTTP puro.

Fluxo:
1. GET /portal.csp -> 302 /index.csp (cookies de sessao)
2. Extrai WEVENT do submit handler do form de login
3. POST %CSP.Broker.cls com cpf+senha (login)
4. GET /portal.csp logado -> extrai WEVENT do "acessarMenu"
5. POST %CSP.Broker.cls com menu=100.3.6.1 -> extrai CSPToken do espelho
6. GET portalRegFrequencia5.csp -> extrai token do grid DataTables
7. POST %CSP.Broker.cls (hyperevent) com mesAno -> JSON das linhas do espelho

Sem browser. Roda em qualquer maquina com Python + requests.
"""
import json
import re
import sys
import time
from datetime import date
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
CACHE_DIR     = ROOT / "cache"
CACHE_SECONDS = 600  # 10 min: mesmo TTL do PDF do Senior

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148"

_WD_ABBR  = ("SEG", "TER", "QUA", "QUI", "SEX", "SAB", "DOM")  # Python weekday()->rotulo do espelho
_RE_LINHA = re.compile(r"^\s*(\d{1,2})\s+(SEG|TER|QUA|QUI|SEX|S[ÁA]B|DOM)\b", re.I)
_RE_HORA  = re.compile(r"(\d{2}:\d{2})")
_RE_TAGS  = re.compile(r"<[^>]+>")
_RE_WS    = re.compile(r"\s+")

def _clean(s: str) -> str:
    s = _RE_TAGS.sub(" ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    return _RE_WS.sub(" ", s).strip()

def _sess() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s

def _login(s: requests.Session):
    print("[login] mentorh")
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

def _baixar_periodo(s: requests.Session, espelho_url: str, ini: date, fim: date) -> list[dict]:
    """Busca as linhas do espelho via hyperevent CSP (DataTables server-side).

    A pagina do espelho nao traz mais a tabela no HTML: o grid carrega por AJAX
    chamando cspHttpServerMethod(token, JSON.stringify(dtReq), matricula, mesAno)
    contra o %CSP.Broker do diretorio da pagina. Retorna a lista de rows JSON.
    """
    r = s.get(espelho_url, timeout=90, headers={
        "Referer": f"{BASE}/portal.csp",
    })
    m = re.search(
        r"cspHttpServerMethod\('([^']+)',JSON\.stringify\(data\),'(\d+)'", r.text)
    if not m:
        raise RuntimeError(f"[mentorh] token do grid nao encontrado na pagina do espelho: {r.text[:300]}")
    token, matricula = m.groups()
    # broker relativo ao diretorio da pagina (cspxmlhttp.js usa URL relativa)
    broker = espelho_url.split("?")[0].rsplit("/", 1)[0] + "/%CSP.Broker.cls"
    # O espelho e mensal: usa o mes de `ini` (requests do monitor/justificativa
    # sao sempre dentro de 1 mes).
    mes_ano = f"{ini.month:02d}/{ini.year}"
    cols = [{"data": c, "name": "", "searchable": True, "orderable": False,
             "search": {"value": "", "regex": False}}
            for c in ("Dia", "CHCodigo", "HorarioTrabalhado", "CHConsiderada",
                      "Saldo", "Ocorrencia")]
    dt_req = {"draw": 1, "columns": cols, "order": [], "start": 0,
              "length": 31, "search": {"value": "", "regex": False}}
    # o servidor leva ~90s para montar o espelho; timeout folgado
    r2 = s.post(broker, timeout=300, headers={
        "Referer": espelho_url,
        "Content-Type": "application/x-www-form-urlencoded",
    }, data={"WARGC": "3", "WEVENT": token, "WARG_1": json.dumps(dt_req),
             "WARG_2": matricula, "WARG_3": mes_ano})
    # resposta hyperevent: linhas de controle, "#OK" e o payload JSON em seguida
    pos = r2.text.find("#OK")
    if pos < 0:
        raise RuntimeError(f"[mentorh] hyperevent sem #OK: {r2.text[:300]}")
    payload = r2.text[pos + 3:].strip()
    try:
        data = json.loads(payload)
    except ValueError as e:
        raise RuntimeError(f"[mentorh] JSON invalido do grid: {payload[:300]}") from e
    rows = data.get("data") or []
    # Guarda: o espelho deve devolver a competencia solicitada; competencia
    # divergente geraria datas/plantoes/extras fantasmas.
    for row in rows:
        got = (row.get("mes_ano") or "").replace("\\", "")
        if got and got != mes_ano:
            raise RuntimeError(
                f"[mentorh] espelho devolveu competencia {got}, solicitada {mes_ano}")
    return rows

def _parse(rows: list[dict], ini: date, fim: date) -> list[dict]:
    out = []
    for row in rows:
        # "Dia": "03  sex"
        m = _RE_LINHA.match(str(row.get("Dia") or ""))
        if not m: continue
        dia = int(m.group(1))
        wk  = m.group(2).upper().replace("Á", "A")[:3]
        d = None
        for cand_mes, cand_ano in ((ini.month, ini.year), (fim.month, fim.year)):
            try:
                cand = date(cand_ano, cand_mes, dia)
                if ini <= cand <= fim:
                    d = cand; break
            except ValueError:
                continue
        if d is None: continue
        # Guarda extra: dia-da-semana da linha deve casar com a data calculada.
        if wk and wk != _WD_ABBR[d.weekday()]:
            raise RuntimeError(
                f"[mentorh] espelho devolveu periodo divergente do solicitado "
                f"[{ini.isoformat()}..{fim.isoformat()}]: linha '{row.get('Dia')}' marca '{wk}', "
                f"mas {d.isoformat()} e '{_WD_ABBR[d.weekday()]}'"
            )
        # cells na mesma ordem das colunas da tabela antiga, p/ manter o
        # contrato com monitor_ponto (cells[1]=CH, cells[2]=horarios/justif).
        cells = [_clean(str(row.get(k) if row.get(k) is not None else ""))
                 for k in ("Dia", "CHCodigo", "HorarioTrabalhado",
                           "CHConsiderada", "Saldo", "Ocorrencia")]
        punches = _RE_HORA.findall(cells[2])
        out.append({
            "date":      d,
            "weekday":   wk,
            "marcacoes": punches,
            "raw":       cells,
        })
    return out

def _cache_path(ini: date, fim: date) -> Path:
    return CACHE_DIR / f"mentorh_{ini.isoformat()}_{fim.isoformat()}.json"

def _load_cache(ini: date, fim: date) -> Optional[list[dict]]:
    p = _cache_path(ini, fim)
    if not p.exists(): return None
    age = time.time() - p.stat().st_mtime
    if age >= CACHE_SECONDS:
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    for d in data:
        d["date"] = date.fromisoformat(d["date"])
    print(f"[cache] reusando {p.name} (idade {int(age)}s)")
    return data

def _save_cache(ini: date, fim: date, dias: list[dict]):
    CACHE_DIR.mkdir(exist_ok=True)
    serial = [{**d, "date": d["date"].isoformat()} for d in dias]
    _cache_path(ini, fim).write_text(json.dumps(serial, ensure_ascii=False), encoding="utf-8")

def baixar(ini: date, fim: date) -> list[dict]:
    cached = _load_cache(ini, fim)
    if cached is not None:
        return cached
    if not USER or not PWD:
        raise RuntimeError("MENTORH_USERNAME/MENTORH_PASSWORD nao configurados no .env")
    s = _sess()
    _login(s)
    print(f"[download] mentorh espelho {ini.isoformat()} a {fim.isoformat()}")
    espelho = _abrir_menu(s, MENU)
    rows = _baixar_periodo(s, espelho, ini, fim)
    dias = _parse(rows, ini, fim)
    _save_cache(ini, fim, dias)
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
