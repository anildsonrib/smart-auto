"""
Monitor de marcacoes de ponto - Senior HCM Waapi.

Logica:
  1. Baixa a folha de ponto do mes corrente via folha_ponto.py
  2. Parseia o PDF -> [{date, weekday, punches[]}]
  3. Para cada dia SEG-QUI das ultimas 14 datas:
     - Se a ultima marcacao esperada (13:00) ainda nao tem 24h -> aguarda
     - Se ja foi notificado (state.json) -> pula
     - Se faltam marcacoes -> notifica via CallMeBot e marca como notificado

Uso:
  python monitor_ponto.py           # roda normal
  python monitor_ponto.py --dry     # nao envia WhatsApp, so mostra
  python monitor_ponto.py --force-day 2026-05-06   # forca checar dia X
  python monitor_ponto.py --no-state                # mostra pendencias sem dedup por state
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, date, timedelta, time
from pathlib import Path

import pdfplumber
import requests

ROOT = Path(__file__).parent
ENV = ROOT / ".env"
STATE = ROOT / "monitor_state.json"
# Script downloader Senior, invocado por subprocess (mesmo nome/local de hoje)
FOLHA_PONTO_SCRIPT = "folha_ponto.py"
# Prefixo dos PDFs Senior baixados (mesma convencao de nome usada hoje)
PDF_PREFIX = "folha_ponto_"

# ----------------------------------------------------------------------
# CONFIG por sistema. Adicionar novos aqui.
# ----------------------------------------------------------------------
SYSTEMS = {
    "st": {  # Santi - Senior HCM / Rubi
        "id": "SANTI",
        "expected":  ["07:00", "10:00", "10:15", "13:00"],
        "weekdays":  {0, 1, 2, 3},
        "downloader": "senior",
    },
    "ss": {  # Sesa - Secullum Ponto 4
        "id": "Sesa",
        "expected":  ["14:00", "20:00"],
        "weekdays":  {0, 1, 2, 3},
        "downloader": "secullum",
    },
    "eb": {  # MentorH (SIGP EBSERH) - escala de plantao 12x36
        "id": "MentorH",
        "expected":  ["07:00", "19:00"],
        "weekdays":  {0, 1, 2, 3, 4, 5, 6},   # qualquer dia pode ser de plantao
        "downloader": "mentorh",
    },
}
DEFAULT_SYSTEM = "st"

# Aguardar N horas apos o ultimo horario esperado antes de avaliar o dia
DELAY_HOURS_AFTER_LAST = 24
# Em /forcar no dia corrente, so considera marcacoes esperadas que estao
# ha pelo menos N horas atras (da tempo do usuario bater o ponto).
FORCE_TODAY_GRACE_HOURS = 4
# Reusa PDF do mes corrente se foi baixado nos ultimos N segundos
PDF_CACHE_SECONDS = 600

# --- .env loader ---
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

# ----------------------------------------------------------------------
# PARSER
# ----------------------------------------------------------------------
RE_LINHA_DIA = re.compile(
    r"^(\d{2}/\d{2})\s+"          # data
    r"(SEG|TER|QUA|QUI|SEX|SAB|DOM)\s+"
    r"(\d{4})"                     # codigo horario (0260 = trabalho; 9996/7/9 = folga)
    r"(.*)$"                       # resto
)
RE_HORA = re.compile(r"\b(\d{2}:\d{2})\b")

def parse_pdf(pdf_path: Path, ini: date, fim: date, expected_count: int | None = None):
    """Retorna lista de {date, weekday, hor_code, punches[], is_workday}.

    Atribui ano a cada linha DD/MM com base no periodo [ini, fim]:
    tenta primeiro ini.year; se a data nao cair no range, tenta fim.year.
    """
    dias = []
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    for raw in text.splitlines():
        m = RE_LINHA_DIA.match(raw.strip())
        if not m: continue
        ddmm, weekday, hor, resto = m.groups()
        d = None
        for cand_ano in (ini.year, fim.year):
            try:
                cand = datetime.strptime(f"{ddmm}/{cand_ano}", "%d/%m/%Y").date()
                if ini <= cand <= fim:
                    d = cand
                    break
            except ValueError:
                continue
        if d is None:
            # fora do range solicitado - ignora
            continue
        horas = RE_HORA.findall(resto)
        # Linha tem: Originais + Tratadas + Trabalho [+ HE60 + HE100 + AdNot + Debito/Credito]
        # 1) Caso normal: Originais == Tratadas -> heuristica simetrica.
        # 2) Caso ajustado por RH: Originais tem menos itens que Tratadas (usuario nao bateu
        #    todos os pontos, RH preencheu via tratativa). Tratadas reflete o que vale.
        #    Pegamos os ultimos `expected_count` valores antes do total Trabalho (penultima
        #    janela). Vale tambem para o caso normal.
        punches = []
        if hor == "0260" and horas:
            for n in range(len(horas) // 2, 0, -1):
                if horas[:n] == horas[n:2*n]:
                    punches = horas[:n]
                    break
            if not punches and expected_count and len(horas) > expected_count:
                punches = horas[-(expected_count + 1):-1]
        # Texto da ocorrencia = "resto" sem as marcacoes (horarios HH:MM)
        motivo = RE_HORA.sub("", resto).strip()
        absent = _has_absence([motivo]) if motivo else False
        dias.append({
            "date":        d,
            "weekday":     weekday,
            "hor_code":    hor,
            "punches":     punches,
            "is_workday":  hor == "0260" and not absent,
            "motivo":      motivo,
        })
    return dias

# ----------------------------------------------------------------------
# DETECTOR
# ----------------------------------------------------------------------
def _hhmm_to_min(s):
    h, m = map(int, s.split(":"))
    return h * 60 + m

def _alinhar(punches, expected):
    """Alinha sequencialmente as marcacoes aos slots esperados minimizando diferenca total.
    Retorna set com indices dos slots esperados que foram cobertos."""
    p = [_hhmm_to_min(t) for t in punches]
    e = [_hhmm_to_min(t) for t in expected]
    k, n = len(p), len(e)
    if k == 0: return set()
    INF = float("inf")
    # dp[i][j] = custo minimo alinhando p[0..i] sendo a i-esima marcacao mapeada para slot e[j]
    dp = [[INF]*n for _ in range(k)]
    parent = [[-1]*n for _ in range(k)]
    for j in range(n):
        dp[0][j] = abs(p[0] - e[j])
    for i in range(1, k):
        for j in range(i, n):
            best, best_prev = INF, -1
            for jp in range(i-1, j):
                if dp[i-1][jp] < best:
                    best = dp[i-1][jp]
                    best_prev = jp
            if best < INF:
                dp[i][j] = abs(p[i] - e[j]) + best
                parent[i][j] = best_prev
    # melhor terminacao
    best_j, best_cost = -1, INF
    for j in range(k-1, n):
        if dp[k-1][j] < best_cost:
            best_cost = dp[k-1][j]
            best_j = j
    if best_j < 0: return set()
    cobertos = set()
    i, j = k-1, best_j
    while i >= 0:
        cobertos.add(j)
        if i == 0: break
        j = parent[i][j]; i -= 1
    return cobertos

def faltas_no_dia(punches, expected):
    """Retorna horarios esperados que nao foram cobertos.

    Logica:
    - 0 marcacoes -> tudo falta
    - >= len(expected) marcacoes -> assume todas cobertas
    - menos marcacoes -> alinha sequencialmente minimizando diferenca total
    """
    if not punches:
        return list(expected)
    if len(punches) >= len(expected):
        return []
    cobertos = _alinhar(punches, expected)
    return [expected[i] for i in range(len(expected)) if i not in cobertos]

def extras_no_dia(punches, expected):
    """Retorna marcacoes que excedem a quantidade esperada no dia.

    Logica:
    - <= len(expected) marcacoes -> nenhuma extra
    - mais marcacoes -> alinha cada esperada com a melhor punch; sobras sao extras.
    """
    if len(punches) <= len(expected) or not expected:
        return []
    cobertos = _alinhar(expected, punches)
    return [punches[i] for i in range(len(punches)) if i not in cobertos]

def avaliar_dias(dias, agora: datetime, sys_cfg: dict,
                 force_day: date | None = None,
                 force_range: tuple[date, date] | None = None,
                 ignore_state: bool = False):
    """Retorna [{date, weekday, faltando[], extras[], punches[]}] dos dias com pendencia que podem ser notificados."""
    pendencias = []
    expected = sys_cfg["expected"]
    weekdays = sys_cfg["weekdays"]
    hoje = agora.date()
    for d in dias:
        # Override per-day (ex: MentorH escala variavel - dia/noite/folga)
        expected_eff = d.get("expected") or expected
        expected_full = expected_eff
        punches_eff = d["punches"]
        if ignore_state:
            corte = agora - timedelta(hours=DELAY_HOURS_AFTER_LAST)
            expected_eff = [
                h for h in expected_eff
                if datetime.combine(d["date"], time(*map(int, h.split(":")))) <= corte
            ]
            punches_eff = [
                h for h in d["punches"]
                if datetime.combine(d["date"], time(*map(int, h.split(":")))) <= corte
            ]
            if not expected_eff:
                continue
        if force_range or force_day:
            if force_range:
                ini, fim = force_range
                if not (ini <= d["date"] <= fim): continue
            else:
                if d["date"] != force_day: continue
            if d["date"] > hoje: continue                     # ignora datas futuras
            if d["date"].weekday() not in weekdays: continue  # respeita dias de trabalho do sistema
            if not d["is_workday"]: continue                  # respeita ABO/FERIAS/folga
            if d["date"] == hoje:
                # so cobra marcacoes esperadas ha pelo menos FORCE_TODAY_GRACE_HOURS
                limite = agora - timedelta(hours=FORCE_TODAY_GRACE_HOURS)
                expected_eff = [h for h in expected_eff
                                if datetime.combine(d["date"], time(*map(int, h.split(":")))) <= limite]
                if not expected_eff: continue
        elif ignore_state:
            if d["date"] > hoje: continue                     # ignora datas futuras
            if d["date"].weekday() not in weekdays: continue  # respeita dias de trabalho do sistema
            if not d["is_workday"]: continue                  # respeita ABO/FERIAS/folga
        else:
            if d["date"].weekday() not in weekdays: continue
            if not d["is_workday"]: continue
            ultimo_h = max(int(x.split(":")[0]) for x in expected)
            ultimo_m = max(int(x.split(":")[1]) for x in expected if x.startswith(f"{ultimo_h:02d}:"))
            deadline = datetime.combine(d["date"], time(ultimo_h, ultimo_m)) + timedelta(hours=DELAY_HOURS_AFTER_LAST)
            if agora < deadline: continue
        faltando = faltas_no_dia(punches_eff, expected_eff)
        # Extras so contam quando o dia foi avaliado contra a escala completa
        # (em --force no dia corrente, expected_eff pode estar filtrado por grace).
        partial = len(expected_eff) < len(expected_full)
        extras = [] if partial else extras_no_dia(punches_eff, expected_full)
        if faltando or extras:
            pendencias.append({
                "date":      d["date"],
                "weekday":   d["weekday"],
                "punches":   punches_eff,
                "faltando":  faltando,
                "extras":    extras,
                "motivo":    d.get("motivo", ""),
            })
    return pendencias

# ----------------------------------------------------------------------
# WHATSAPP
# ----------------------------------------------------------------------
def send_whatsapp(env, message: str, skip: bool = False):
    if skip:
        print("[whatsapp] skip (--no-whatsapp)")
        return True
    phone  = env.get("CALLMEBOT_PHONE", "").strip()
    apikey = env.get("CALLMEBOT_APIKEY", "").strip()
    if not phone or not apikey:
        print("[whatsapp] CALLMEBOT_PHONE/APIKEY nao configurados, pulando envio.")
        return False
    r = requests.get(
        "https://api.callmebot.com/whatsapp.php",
        params={"phone": phone, "text": message, "apikey": apikey},
        timeout=20,
    )
    ok = r.status_code == 200 and "queued" in r.text.lower() or "success" in r.text.lower()
    print(f"[whatsapp] status={r.status_code} resp={r.text[:100]}")
    return ok

def _erro_curto(exc: BaseException) -> str:
    msg = str(exc).strip()
    if not msg:
        msg = exc.__class__.__name__
    return msg[:180] + ("..." if len(msg) > 180 else "")

def notificar_falha(env: dict, ident: str, exc: BaseException, skip: bool = False):
    msg = (
        f"*{ident}*\n\n"
        "*Falha ao checar ponto*\n\n"
        "Nao consegui baixar/avaliar a folha agora. "
        "A proxima execucao tentara novamente.\n\n"
        f"_Erro:_\n   {_erro_curto(exc)}"
    )
    print("\n--- Mensagem ---\n" + msg + "\n")
    return send_whatsapp(env, msg, skip=skip)

# ----------------------------------------------------------------------
# STATE (namespaced por sistema)
# ----------------------------------------------------------------------
def _load_all_state():
    if not STATE.exists(): return {}
    data = json.loads(STATE.read_text(encoding="utf-8"))
    # Migracao retro-compat: se nao tiver namespace, assume tudo era "st"
    if not any(k in data for k in SYSTEMS):
        return {"st": data}
    return data

def load_state(sys_key: str) -> dict:
    return _load_all_state().get(sys_key, {})

def save_state(sys_key: str, state_dict: dict):
    all_state = _load_all_state()
    all_state[sys_key] = state_dict
    STATE.write_text(json.dumps(all_state, indent=2, default=str), encoding="utf-8")

# ----------------------------------------------------------------------
# DOWNLOAD do PDF (chama folha_ponto.py)
# ----------------------------------------------------------------------
def baixar_pdf_periodo(ini: date, fim: date) -> Path:
    """Baixa folha de ponto cobrindo [ini, fim] (pode atravessar meses).

    Cache: se ja existe PDF com mesmo periodo e mtime < PDF_CACHE_SECONDS, reusa.
    """
    import time as _time
    nome = f"{PDF_PREFIX}{ini.strftime('%d-%m-%Y')}_{fim.strftime('%d-%m-%Y')}.pdf"
    alvo = ROOT / nome
    if alvo.exists():
        age = _time.time() - alvo.stat().st_mtime
        if age < PDF_CACHE_SECONDS:
            print(f"[cache] reusando {alvo.name} (idade {int(age)}s)")
            return alvo

    print(f"[download] baixando folha {ini.isoformat()} a {fim.isoformat()}...")
    res = subprocess.run(
        [sys.executable, str(ROOT / FOLHA_PONTO_SCRIPT), ini.isoformat(), fim.isoformat()],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    if res.returncode != 0:
        print(res.stdout); print(res.stderr)
        raise RuntimeError(f"{FOLHA_PONTO_SCRIPT} falhou")
    if not alvo.exists():
        # Fallback: pega o PDF mais recente
        pdfs = sorted(ROOT.glob(f"{PDF_PREFIX}*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not pdfs: raise RuntimeError("PDF nao encontrado apos download")
        return pdfs[0]
    return alvo

# ----------------------------------------------------------------------
# DISPATCHER: baixa+normaliza dias conforme sistema
# ----------------------------------------------------------------------
_ABSENCE_TOKENS = ("FERIAS","ABO","ABONO","FALTA","FOLGA","FERIADO","DSR","ATESTADO",
                   "PONTO FACULTATIVO","FACULTATIVO")

def _has_absence(raw_cells):
    for c in raw_cells or []:
        cu = c.upper().strip()
        if any(cu == t or cu.startswith(t) for t in _ABSENCE_TOKENS):
            return True
    return False

def baixar_dias(sys_key: str, ini: date, fim: date) -> list[dict]:
    """Retorna list[{date, weekday(UPPER 3), punches[], is_workday}] normalizado."""
    cfg = SYSTEMS[sys_key]
    if cfg["downloader"] == "senior":
        pdf_path = baixar_pdf_periodo(ini, fim)
        print(f"[pdf] {pdf_path}")
        return parse_pdf(pdf_path, ini, fim, expected_count=len(cfg["expected"]))
    elif cfg["downloader"] == "secullum":
        import folha_secullum
        raw = folha_secullum.baixar(ini, fim)
        dias = []
        for d in raw:
            absent = _has_absence(d.get("raw", []))
            dias.append({
                "date":       d["date"],
                "weekday":    (d.get("weekday") or "")[:3].upper(),
                "punches":    d.get("marcacoes", []),
                "is_workday": d["date"].weekday() in cfg["weekdays"] and not absent,
                "motivo":     " ".join(c for c in d.get("raw", []) if c and not re.match(r"^\d{2}:\d{2}", c.strip())).strip(),
            })
        return dias
    elif cfg["downloader"] == "mentorh":
        import folha_mentorh
        raw = folha_mentorh.baixar(ini, fim)
        dias = []
        for d in raw:
            cells = d.get("raw", [])
            ch_code  = (cells[1] if len(cells) > 1 else "").strip()
            ocorrencia = " ".join(cells).upper()
            absent  = _has_absence(cells)
            sem_dados = "COLETA" in ocorrencia and ("NAO" in ocorrencia or "NÃO" in ocorrencia)
            punches = d.get("marcacoes", [])
            # Coluna de marcacoes (cell[2]) pode trazer TEXTO no lugar dos horarios
            # (ex.: "Ponto Facultativo", "Sabado", "Feriado"): e a propria
            # justificativa do dia. Sem marcacoes + texto ali => dia nao e a cobrar.
            marc_col = (cells[2] if len(cells) > 2 else "").strip()
            just_no_lugar = (not punches) and bool(re.search(r"[A-Za-zÀ-ÿ]", marc_col))
            wk = d["date"].weekday()  # 0=seg ... 4=sex ... 6=dom
            # Escala MentorH:
            #  - regular: sex/dom 07:00-19:00 (diurno)
            #  - extra:   noite (qualquer dia) 19:00 dia X -> 07:00 dia X+1
            # Detecta noite quando a primeira marcacao do dia e >= 17:00
            primeira = int(punches[0].split(":")[0]) if punches else None
            if primeira is not None and primeira >= 17:
                expected_dia = ["19:00"]                          # so a entrada cabe no dia
            elif primeira is not None and primeira < 12 and wk not in (4, 6):
                expected_dia = ["07:00"]                          # saida de plantao da noite anterior
            else:
                expected_dia = ["07:00", "19:00"]                 # diurno padrao (sex/dom)
            ocorrencia_txt = (cells[5] if len(cells) > 5 else "").strip()
            obs_txt        = (cells[6] if len(cells) > 6 else "").strip()
            motivo = " ".join(s for s in (marc_col if just_no_lugar else "", ocorrencia_txt, obs_txt) if s).strip()
            dias.append({
                "date":       d["date"],
                "weekday":    (d.get("weekday") or "")[:3].upper(),
                "punches":    punches,
                "is_workday": bool(ch_code) and not absent and not sem_dados and not just_no_lugar,
                "expected":   expected_dia,
                "motivo":     motivo,
            })
        return dias
    else:
        raise ValueError(f"downloader desconhecido: {cfg['downloader']}")

# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", choices=list(SYSTEMS.keys()), default=DEFAULT_SYSTEM, help="sistema de ponto: st (Santi/Senior) ou ss (Sesa/Secullum)")
    ap.add_argument("--dry", action="store_true", help="nao envia WhatsApp, apenas mostra")
    ap.add_argument("--force-day", type=str, help="forca avaliar uma data especifica YYYY-MM-DD (ignora regra de 24h)")
    ap.add_argument("--force-range", nargs=2, metavar=("INI","FIM"), help="forca avaliar um intervalo YYYY-MM-DD YYYY-MM-DD")
    ap.add_argument("--pdf", type=str, help="(so st) usar PDF local em vez de baixar")
    ap.add_argument("--no-whatsapp", action="store_true", help="nao envia WhatsApp (so processa e atualiza state)")
    ap.add_argument("--no-state", action="store_true", help="mostra as pendencias sem dedup por state")
    ap.add_argument("--quiet-failure", action="store_true", help="nao envia WhatsApp de falha (usado pelo retry horario)")
    ap.add_argument("--subtitulo", type=str, default="", help="linha extra no cabecalho da mensagem, logo abaixo do identificador do sistema")
    args = ap.parse_args()
    sys_cfg = SYSTEMS[args.system]
    IDENT = sys_cfg["id"]
    print(f"[system] {args.system} ({IDENT})")

    env = load_env()
    agora = datetime.now()
    force_day = datetime.strptime(args.force_day, "%Y-%m-%d").date() if args.force_day else None
    force_range = None
    if args.force_range:
        ini = datetime.strptime(args.force_range[0], "%Y-%m-%d").date()
        fim = datetime.strptime(args.force_range[1], "%Y-%m-%d").date()
        if ini > fim: ini, fim = fim, ini
        force_range = (ini, fim)

    # 1. Define periodo a baixar
    today = date.today()
    if force_range:
        per_ini, per_fim = force_range
    elif force_day:
        per_ini = force_day.replace(day=1)
        from calendar import monthrange
        per_fim = force_day.replace(day=monthrange(force_day.year, force_day.month)[1])
    else:
        from calendar import monthrange
        per_ini = today.replace(day=1)
        per_fim = today.replace(day=monthrange(today.year, today.month)[1])

    # 2. Download + normaliza (sistema-aware)
    try:
        if args.pdf and sys_cfg["downloader"] == "senior":
            dias = parse_pdf(Path(args.pdf), per_ini, per_fim)
        else:
            dias = baixar_dias(args.system, per_ini, per_fim)
    except Exception as exc:
        print(f"[erro] {exc.__class__.__name__}: {exc}")
        if not args.dry and not args.force_day and not args.force_range and not args.quiet_failure:
            notificar_falha(env, IDENT, exc, skip=args.no_whatsapp)
        raise
    print(f"[parse] {len(dias)} linhas de dia extraidas")

    # 3. Avalia + filtra com state
    pendencias = avaliar_dias(dias, agora, sys_cfg, force_day=force_day, force_range=force_range, ignore_state=args.no_state)
    state = {} if args.no_state else load_state(args.system)
    novos = []
    for p in pendencias:
        key = p["date"].isoformat()
        if not args.no_state and not args.force_day and not args.force_range and key in state:
            print(f"[skip] {key} ja notificado em {state[key]}")
            continue
        novos.append(p)

    # 4. Sem nada novo a notificar -> mensagem semanal amigavel (1x por semana ISO)
    if not novos:
        print(f"[ok] nada novo a notificar ({len(pendencias)} pendencia(s) ja conhecida(s)).")
        ok_key = f"ok_{agora.strftime('%G-W%V')}"
        if not args.no_state and not args.force_day and not args.force_range and ok_key not in state and not args.dry:
            msg = f"*{IDENT}*\n\n*Ponto OK*\n\nSemana fechada sem pendencias. Bom fim de semana!"
            print("\n--- Mensagem ---\n" + msg + "\n")
            if send_whatsapp(env, msg, skip=args.no_whatsapp):
                state[ok_key] = agora.isoformat(timespec="seconds")
                save_state(args.system, state)
                print(f"[state] {ok_key} registrado.")
        else:
            print(f"[ok] mensagem semanal pulada (key={ok_key}, dry={args.dry}, force={bool(args.force_day)}, no_state={args.no_state}).")
        return

    # 5. Monta mensagem (formato otimizado pra WhatsApp - evita auto-link de datas/horas)
    SEMANA_LONGA = {"SEG":"Segunda","TER":"Terca","QUA":"Quarta","QUI":"Quinta","SEX":"Sexta","SAB":"Sabado","DOM":"Domingo"}
    def bloco(p):
        dia = p["date"].strftime("%d/%m")
        wk  = SEMANA_LONGA.get(p["weekday"], p["weekday"])
        marcadas_lst = "\n".join(f"   {h}" for h in p["punches"]) if p["punches"] else "   (nenhuma)"
        motivo = (p.get("motivo") or "").strip()
        rodape = f"\n\n_Motivo na folha:_\n   {motivo}" if (not p["punches"] and motivo) else ""
        secoes = [f"*{wk}, {dia}*"]
        if p.get("faltando"):
            faltando_lst = "\n".join(f"   {h}" for h in p["faltando"])
            secoes.append(f"_Faltaram:_\n{faltando_lst}")
        if p.get("extras"):
            extras_lst = "\n".join(f"   {h}" for h in p["extras"])
            secoes.append(f"_Marcacoes extras:_\n{extras_lst}")
        secoes.append(f"_Marcadas:_\n{marcadas_lst}")
        return "\n\n".join(secoes) + rodape

    subt = (args.subtitulo or "").strip()
    cabecalho = f"*{IDENT}*\n" + (f"\n{subt}\n" if subt else "") + f"\n*Ponto pendente*\n\n"
    if len(novos) == 1:
        msg = cabecalho + bloco(novos[0])
    else:
        msg = cabecalho + "\n\n- - - - -\n\n".join(bloco(p) for p in novos)

    print("\n--- Mensagem ---\n" + msg + "\n")

    # 6. Envia
    if args.dry:
        print("[dry] modo dry-run, nao enviou.")
        return
    if send_whatsapp(env, msg, skip=args.no_whatsapp):
        if args.no_state:
            print("[state] no-state ativo; state nao foi atualizado.")
            return
        for p in novos:
            state[p["date"].isoformat()] = agora.isoformat(timespec="seconds")
        save_state(args.system, state)
        print(f"[state] atualizado com {len(novos)} dia(s).")

if __name__ == "__main__":
    main()
