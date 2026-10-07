"""
Notificacao de justificativa por sistema, com regras especificas e catch-up.

Regras (RULES dict):
- st  (Santi/Senior RH): dias 7 e 20 do mes, as 09h. Se cair em sex/sab/dom,
  dispara na quinta anterior E na segunda seguinte (justificativa manual,
  exige presenca fisica). Sao DUAS entregas ao RH sobre o mesmo ciclo, com
  cortes diferentes: dia 7 -> 21 do mes anterior ate dia 6; dia 20 -> 21 do
  mes anterior ate dia 20.
- ss  (Sesa/Secullum) e eb (MentorH/EBSERH): dia 1 do mes, as 16h, sem shift
  (justificativa pode ser feita online).

Em todos os casos: chama monitor_ponto.py --force-range com o periodo da regra.
--force-range bypassa dedup mas preserva carencia de 4h e deteccao de motivo
na folha (atestado etc). So envia WhatsApp se houver pendencia real.

CATCH-UP
--------
Antes, o disparo dependia de `hoje` ser exatamente a data alvo. Se a VM
estivesse desligada naquele dia (ou o monitor falhasse), a justificativa era
perdida em silencio - e o checar_retry.py nao cobre esse caso, porque ele
registra falha de execucao, nao ausencia de execucao.

Agora cada data de disparo e um evento identificavel, registrado em
justificativa_state.json quando cumprido com sucesso. A cada execucao o script
procura datas de disparo dos ultimos CATCHUP_MAX_DAYS dias que ainda nao foram
cumpridas e dispara as pendentes, mesmo fora do dia exato.

Consequencias:
- O range e calculado a partir da DATA DE DISPARO, nao de `hoje`. Um catch-up
  no dia 23 referente ao disparo do dia 21 avalia o ciclo do dia 21.
- Marca como cumprido somente com exit 0. Se o monitor falhar, a data segue
  pendente e e retentada na proxima execucao (dentro da janela).
- Disparos que geram o mesmo range sao coalescidos em um unico envio (evita
  duas mensagens quase identicas quando as 2 datas do st ficam pendentes).
- Datas mais antigas que CATCHUP_MAX_DAYS sao descartadas com log, sem envio
  (mesma filosofia do RETRY_MAX_HOURS do checar_retry.py).

Estado:
  justificativa_state.json (runtime, fora do git). Escrita atomica + lock local.
  Estrutura: {"st": {"despachados": ["2026-07-08", "2026-07-21"]}, ...}

Uso:
  python justificativa.py --system st
  python justificativa.py --system ss --dry-run
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
MONITOR = ROOT / "monitor_ponto.py"
STATE = ROOT / "justificativa_state.json"
LOCK = ROOT / ".justificativa_state.lock"

# Janela de catch-up: um disparo perdido ainda vale a pena dentro desse prazo.
CATCHUP_MAX_DAYS = 5
# Retencao do historico de despachados, so para nao crescer sem limite.
HISTORY_KEEP_DAYS = 120

LOCK_STALE_SECONDS = 60
LOCK_WAIT_SECONDS = 15

RULES = {
    "st": {
        # Dias 7 e 20 (confirmado pelo usuario em 07/10/2026). Historico: nasceu
        # como [7, 20] em b38be29, virou [8, 21] em d983ce2 a pedido do usuario,
        # e voltou a [7, 20] - a janela real de entrega ao RH e dia 7 e dia 20.
        "days": [7, 20],
        "weekend_shift": True,
        # st: a competencia do RH Senior abre no dia 21. O usuario entrega DUAS
        # justificativas sobre o mesmo ciclo, com cortes diferentes:
        #   disparo dia  7 -> 21 do mes anterior ate dia  6 (parcial, meio do ciclo)
        #   disparo dia 20 -> 21 do mes anterior ate dia 20 (ciclo fechado)
        # Por isso o fim da janela depende do DIA-ALVO da regra, nao da data do
        # disparo (que o weekend_shift pode deslocar). Datas futuras sao
        # ignoradas pelo monitor (--force-range).
        "periodo": "ciclo21",
        "janela_fim": {7: 6, 20: 20},
        "subtitulo": "Chegou o dia de fazer a justificativa de ponto em Santi RH. Verifique as pendencias abaixo:",
    },
    "ss": {
        "days": [1],
        "weekend_shift": False,
        # ss/eb: justificativa online no dia 1, cobre o mes anterior fechado
        "periodo": "mes_anterior",
        "subtitulo": "Chegou o dia de fazer a justificativa de ponto em Sesa. Justificativa pode ser feita online.",
    },
    "eb": {
        "days": [1],
        "weekend_shift": False,
        "periodo": "mes_anterior",
        "subtitulo": "Chegou o dia de fazer a justificativa de ponto em MentorH. Justificativa pode ser feita online.",
    },
}


# ----------------------------------------------------------------------
# LOCK (mesma abordagem do checar_retry.py)
# ----------------------------------------------------------------------
class _Lock:
    """Lock de arquivo cross-platform via O_CREAT|O_EXCL. Sem dependencia externa."""

    def __init__(self, path: Path):
        self.path = path
        self.fd = None

    def __enter__(self):
        deadline = time.time() + LOCK_WAIT_SECONDS
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > LOCK_STALE_SECONDS:
                        self.path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.time() >= deadline:
                    raise TimeoutError(f"nao obteve lock {self.path} em {LOCK_WAIT_SECONDS}s")
                time.sleep(0.3)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


# ----------------------------------------------------------------------
# STATE (atomico)
# ----------------------------------------------------------------------
def load_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict):
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    os.replace(str(tmp), str(STATE))


def despachados(state: dict, system: str) -> set[str]:
    return set((state.get(system) or {}).get("despachados") or [])


def marcar_despachados(system: str, datas: list[date], hoje: date):
    """Registra datas de disparo como cumpridas e poda historico antigo."""
    with _Lock(LOCK):
        state = load_state()
        entry = state.setdefault(system, {})
        atuais = set(entry.get("despachados") or [])
        atuais |= {d.isoformat() for d in datas}
        corte = hoje - timedelta(days=HISTORY_KEEP_DAYS)
        entry["despachados"] = sorted(
            s for s in atuais if _parse_iso(s) is None or _parse_iso(s) >= corte
        )
        save_state(state)


def _parse_iso(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


# ----------------------------------------------------------------------
# DATAS DE DISPARO
# ----------------------------------------------------------------------
def datas_notificacao(year: int, month: int, target_day: int, weekend_shift: bool) -> list[date]:
    """Datas reais de disparo para uma target_day do mes."""
    d = date(year, month, target_day)
    if not weekend_shift:
        return [d]
    wk = d.weekday()  # 0=seg ... 6=dom
    if wk <= 3:
        return [d]
    thu = d - timedelta(days=wk - 3)        # ultima quinta antes
    mon = d + timedelta(days=(7 - wk))      # proxima segunda
    return [thu, mon]


def _meses_vizinhos(hoje: date) -> list[tuple[int, int]]:
    """(ano, mes) do mes anterior, corrente e seguinte.

    Cobre disparos que o weekend_shift empurra para fora do mes de origem.
    """
    ant = hoje.replace(day=1) - timedelta(days=1)
    prox = (hoje.replace(day=28) + timedelta(days=7)).replace(day=1)
    return [(ant.year, ant.month), (hoje.year, hoje.month), (prox.year, prox.month)]


def todas_as_datas(hoje: date, rule: dict) -> list[tuple[date, int, date]]:
    """Disparos da regra nos meses vizinhos: (data_disparo, dia_alvo, mes_alvo).

    O dia-alvo viaja junto porque o `weekend_shift` desloca a data de disparo e,
    no `st`, o fim da janela depende do alvo (7 -> dia 6; 20 -> dia 20), nao da
    data em que a notificacao sai. `mes_alvo` (dia 1 do mes do alvo) ancora a
    competencia mesmo se um deslocamento cruzar a virada de mes.
    """
    out = {}
    for year, month in _meses_vizinhos(hoje):
        for td in rule["days"]:
            alvo_mes = date(year, month, 1)
            for d in datas_notificacao(year, month, td, rule["weekend_shift"]):
                out[d] = (d, td, alvo_mes)
    return [out[k] for k in sorted(out)]


def datas_pendentes(hoje: date, rule: dict, ja_feitas: set[str]):
    """Separa disparos devidos em (pendentes na janela, expirados).

    Devido = data <= hoje e ainda nao cumprido. Expirado = devido mas mais
    antigo que CATCHUP_MAX_DAYS. Cada item e a tupla de `todas_as_datas`.
    """
    limite = hoje - timedelta(days=CATCHUP_MAX_DAYS)
    pendentes, expiradas = [], []
    for item in todas_as_datas(hoje, rule):
        d = item[0]
        if d > hoje or d.isoformat() in ja_feitas:
            continue
        (pendentes if d >= limite else expiradas).append(item)
    return pendentes, expiradas


# ----------------------------------------------------------------------
# PERIODO
# ----------------------------------------------------------------------
def periodo_range(ref: date, rule: dict, dia_alvo: int | None = None,
                  mes_alvo: date | None = None) -> tuple[date, date]:
    """Retorna (ini, fim) do range a avaliar conforme o periodo da regra.

    `ref` e a DATA DE DISPARO, nao a data de hoje: num catch-up o ciclo avaliado
    tem de ser o do disparo perdido, nao o do dia em que se percebeu.

    `dia_alvo`/`mes_alvo` vem da regra, nao do calendario: no `st` o disparo pode
    ser deslocado pelo weekend_shift, e o corte da janela segue o dia-alvo
    (7 -> dia 6; 20 -> dia 20), nao a data em que a mensagem saiu.
    """
    p = rule["periodo"]
    base = mes_alvo or ref.replace(day=1)
    if p == "mes_anterior":
        # ss/eb: mes anterior fechado (dia 1 ao ultimo dia do mes anterior)
        fim_d = base - timedelta(days=1)
        ini_d = fim_d.replace(day=1)
    elif p == "ciclo21":
        # st: competencia abre no dia 21 do mes anterior; o fim depende do alvo.
        ini_d = (base - timedelta(days=1)).replace(day=21)
        fim_dia = (rule.get("janela_fim") or {}).get(dia_alvo, 20)
        fim_d = base.replace(day=fim_dia)
    else:  # mes_corrente: dia 1 do mes corrente ate a data de referencia
        ini_d = base
        fim_d = ref
    return ini_d, fim_d


def run_monitor(system: str, ini: str, fim: str, subtitulo: str) -> int:
    return subprocess.run(
        [sys.executable, str(MONITOR),
         "--system", system, "--force-range", ini, fim,
         "--subtitulo", subtitulo],
        cwd=ROOT,
    ).returncode


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Notificacao de justificativa com catch-up.")
    ap.add_argument("--system", choices=list(RULES.keys()), required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="mostra o que seria disparado, sem chamar o monitor nem gravar state")
    ap.add_argument("--hoje", help="data de referencia YYYY-MM-DD (teste)")
    args = ap.parse_args()

    rule = RULES[args.system]
    hoje = _parse_iso(args.hoje) if args.hoje else date.today()
    if hoje is None:
        ap.error("--hoje precisa ser YYYY-MM-DD")

    state = load_state()
    # Sem entrada no state = primeira execucao (ou state perdido). Nesse caso as
    # datas antigas nao sao "perdidas", so nunca foram rastreadas: absorve em
    # silencio em vez de despejar uma linha de descarte por data historica.
    bootstrap = args.system not in state
    ja_feitas = despachados(state, args.system)
    pendentes, expiradas = datas_pendentes(hoje, rule, ja_feitas)

    datas_expiradas = [it[0] for it in expiradas]
    if bootstrap:
        if expiradas:
            print(f"[init] {args.system}: state inicializado, {len(expiradas)} disparo(s) "
                  f"anteriores a janela marcados como cumpridos "
                  f"({datas_expiradas[0]}..{datas_expiradas[-1]}).")
    else:
        for d in datas_expiradas:
            print(f"[skip] {args.system}: disparo {d} passou de {CATCHUP_MAX_DAYS} dias, "
                  f"descartado sem envio.")
    if expiradas and not args.dry_run:
        marcar_despachados(args.system, datas_expiradas, hoje)

    if not pendentes:
        print(f"[skip] {hoje}: nada devido para justificativa {args.system}.")
        return 0

    # Coalesce: disparos distintos que avaliam o mesmo range viram um envio.
    # No `st` os dois disparos do ciclo tem cortes diferentes (dia 6 e dia 20),
    # entao nao coalescem - sao duas entregas distintas ao RH, como esperado.
    por_range: dict[tuple[str, str], list[date]] = {}
    for d, dia_alvo, mes_alvo in pendentes:
        ini_d, fim_d = periodo_range(d, rule, dia_alvo, mes_alvo)
        por_range.setdefault((ini_d.isoformat(), fim_d.isoformat()), []).append(d)

    rc_final = 0
    for (ini, fim), datas in sorted(por_range.items()):
        atraso = (hoje - max(datas)).days
        tag = "run" if atraso == 0 else f"catch-up +{atraso}d"
        print(f"[{tag}] justificativa {args.system} {ini} a {fim} "
              f"(disparo(s): {', '.join(d.isoformat() for d in datas)})")
        if args.dry_run:
            continue
        rc = run_monitor(args.system, ini, fim, rule["subtitulo"])
        if rc == 0:
            marcar_despachados(args.system, datas, hoje)
        else:
            print(f"[falha] {args.system}: monitor exit {rc}; disparo(s) seguem pendentes "
                  f"e serao retentados na proxima execucao.")
            rc_final = rc
    return rc_final


if __name__ == "__main__":
    sys.exit(main())
