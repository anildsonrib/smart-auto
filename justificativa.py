"""
Notificacao de justificativa por sistema, com regras especificas.

Regras (RULES dict):
- st  (Santi/Senior RH): dias 7 e 20 do mes, as 18h. Se cair em sex/sab/dom,
  dispara na quinta anterior E na segunda seguinte (justificativa manual,
  exige presenca fisica).
- ss  (Sesa/Secullum) e eb (MentorH/EBSERH): dia 1 do mes, as 16h, sem shift
  (justificativa pode ser feita online).

Em todos os casos: chama monitor_ponto.py --force-range com o mes corrente.
--force-range bypassa dedup mas preserva carencia de 4h e deteccao de motivo
na folha (atestado etc). So envia WhatsApp se houver pendencia real.

Uso:
  python justificativa.py --system st
  python justificativa.py --system ss
  python justificativa.py --system eb
"""
import argparse
import subprocess
import sys
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
HOJE = date.today()

RULES = {
    "st": {
        "days": [7, 20],
        "weekend_shift": True,
        # st: justificativa manual no dia, checa o que ja se acumulou no mes corrente
        "periodo": "mes_corrente",
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


def deve_notificar(hoje: date, rule: dict) -> bool:
    datas = []
    for td in rule["days"]:
        datas += datas_notificacao(hoje.year, hoje.month, td, rule["weekend_shift"])
    return hoje in datas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", choices=list(RULES.keys()), required=True)
    args = ap.parse_args()
    rule = RULES[args.system]

    if not deve_notificar(HOJE, rule):
        print(f"[skip] {HOJE} nao e dia de justificativa {args.system}.")
        return 0

    if rule["periodo"] == "mes_anterior":
        primeiro_dia_mes = HOJE.replace(day=1)
        fim_d = primeiro_dia_mes - timedelta(days=1)
        ini_d = fim_d.replace(day=1)
    else:
        ini_d = HOJE.replace(day=1)
        fim_d = HOJE
    ini, fim = ini_d.isoformat(), fim_d.isoformat()
    print(f"[run] justificativa {args.system} {ini} a {fim}")
    r = subprocess.run(
        [sys.executable, str(ROOT / "monitor_ponto.py"),
         "--system", args.system, "--force-range", ini, fim,
         "--subtitulo", rule["subtitulo"]],
        cwd=ROOT,
    )
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
