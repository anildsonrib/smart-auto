"""Downloader do espelho de ponto MentorH (SIGP EBSERH).

Conecta no Chrome ja aberto via CDP (sessao em active-sessions/portal-mentorh.lnk),
faz login se necessario, abre o iframe do menu "Folha de Frequencia" (codigo 100.3.6.1),
ajusta o periodo e parseia a tabela do espelho.

Interface igual a folha_secullum: `baixar(ini, fim) -> list[dict]`
  cada item: {date, weekday(3 letras), marcacoes[HH:MM], raw[celulas]}
"""
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
ENV  = ROOT / ".env"

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
CDP_URL  = f"http://127.0.0.1:{ENV_VARS.get('MENTORH_CDP_PORT', '9231')}"
PORTAL   = ENV_VARS.get("MENTORH_BASE_URL",
                        "https://sigp.ebserh.gov.br/csp/ebserh/portal/novo/portal.csp")
USERNAME = ENV_VARS.get("MENTORH_USERNAME", "")
PASSWORD = ENV_VARS.get("MENTORH_PASSWORD", "")

# Menu code do "Folha de Frequencia" / "Registro de Frequencia"
MENU_CODE = "100.3.6.1"
# Cache $HOROLOG: dias desde 1840-12-31 (Intersystems Cache)
EPOCH = date(1840, 12, 31)

_RE_LINHA = re.compile(r"^\s*(\d{1,2})\s+(SEG|TER|QUA|QUI|SEX|S[ÁA]B|DOM)\b", re.I)
_RE_HORA  = re.compile(r"(\d{2}:\d{2})")
_ABSENT_TOKENS = ("ABONO", "ABO", "FERIAS", "FOLGA", "FERIADO", "ATESTADO", "AFAST")

def _to_horolog(d: date) -> int:
    return (d - EPOCH).days

def _login_if_needed(page):
    try:
        if page.locator("#login").is_visible(timeout=2000):
            print("[login] sessao deslogada, autenticando...")
            page.locator("#login").fill(USERNAME)
            page.locator("#senha").fill(PASSWORD)
            try:
                with page.expect_navigation(timeout=15000):
                    page.locator("#acessar").click()
            except Exception:
                page.wait_for_timeout(3000)
            # aguarda barra de menu carregar (indica portal autenticado)
            try:
                page.wait_for_selector("[menu]", timeout=15000)
            except Exception:
                pass
    except Exception:
        pass

def _open_frequencia(page):
    """Garante que o iframe da folha esta carregado e retorna o Frame."""
    page.wait_for_function(f"window.jQuery && document.querySelector(\"[menu='{MENU_CODE}']\") !== null", timeout=15000)
    page.evaluate(f"jQuery(\"[menu='{MENU_CODE}']\").trigger('click')")
    # aguarda o iframe portalRegFrequencia aparecer (ate 15s)
    for _ in range(30):
        frame = next((f for f in page.frames if "portalRegFrequencia" in f.url), None)
        if frame: break
        page.wait_for_timeout(500)
    else:
        raise RuntimeError("[mentorh] iframe portalRegFrequencia nao encontrado")
    frame.wait_for_load_state("domcontentloaded", timeout=15000)
    return frame

def _set_periodo(page, frame, ini: date, fim: date):
    """Resubmete o form com o periodo solicitado (horolog)."""
    horolog_ini = _to_horolog(ini)
    horolog_fim = _to_horolog(fim)
    frame.evaluate(f"""
        (function() {{
          const f = document.forms[0];
          f.querySelector('[name=atualPrimData]').value = '{horolog_ini}';
          f.querySelector('[name=atualUltData]').value  = '{horolog_fim}';
          f.submit();
        }})()
    """)
    page.wait_for_timeout(1500)
    # Apos submit o iframe recarrega; precisa pegar referencia nova
    frame = next((f for f in page.frames if "portalRegFrequencia" in f.url), frame)
    frame.wait_for_load_state("domcontentloaded", timeout=15000)
    page.wait_for_timeout(800)
    return frame

def _parse_frame(frame, ini: date, fim: date) -> list[dict]:
    """Extrai linhas da tabela do espelho."""
    rows = frame.evaluate("""
        Array.from(document.querySelectorAll('table tr'))
          .map(r => Array.from(r.cells).map(c => c.innerText.trim()))
          .filter(cs => cs.length >= 4 && /^\\d+\\s+\\w{3}/i.test(cs[0]))
    """)
    out = []
    for cells in rows:
        m = _RE_LINHA.match(cells[0])
        if not m: continue
        dia = int(m.group(1))
        wk  = m.group(2).upper().replace("Á", "A")[:3]
        ano, mes = ini.year, ini.month
        # tenta atribuir mes/ano corretos para datas dentro de [ini, fim]
        d = None
        for cand_mes, cand_ano in ((ini.month, ini.year), (fim.month, fim.year)):
            try:
                cand = date(cand_ano, cand_mes, dia)
                if ini <= cand <= fim:
                    d = cand
                    break
            except ValueError:
                continue
        if d is None: continue
        # marcacoes: pares "HH:MMr - HH:MMr" na coluna 2 (Horario Trabalhado)
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
    """Baixa marcacoes do MentorH para o periodo [ini, fim]."""
    print(f"[mentorh] periodo {ini.isoformat()} a {fim.isoformat()}")
    if not USERNAME or not PASSWORD:
        raise RuntimeError("MENTORH_USERNAME/MENTORH_PASSWORD nao configurados no .env")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            raise RuntimeError(
                f"[mentorh] Chrome com CDP nao encontrado em {CDP_URL}. "
                f"Abra active-sessions/portal-mentorh.lnk. Detalhe: {e}"
            )
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if "ebserh" not in page.url:
            page.goto(PORTAL, wait_until="domcontentloaded")
        _login_if_needed(page)
        frame = _open_frequencia(page)
        frame = _set_periodo(page, frame, ini, fim)
        dias = _parse_frame(frame, ini, fim)
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
