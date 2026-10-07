"""Calendario de feriados brasileiros - fonte independente da folha de ponto.

Por que existe: sistemas de ponto nao sao confiaveis para informar feriado.
O Senior (st) traz codigo de horario != 0260 no dia e o monitor ja ignora, mas
o Secullum (ss) nao traz nada - 07/09/2026 (Independencia, segunda) foi cobrado
como falta. Para **diarista**, feriado nao e dia de trabalho; para plantonista
(eb/MentorH) feriado e dia normal de escala. Por isso a regra e opt-in por
sistema, via `feriado_folga` no dict SYSTEMS.

Cadeia de resolucao, nesta ordem:

1. `feriados_extra.json` (raiz, fora do git) - estaduais/municipais declarados
   manualmente. Tem precedencia: e conhecimento local que nenhuma API nacional
   tem. Formato: {"2026-10-24": "Aniversario da cidade"}
2. BrasilAPI (https://brasilapi.com.br/api/feriados/v1/<ano>) - feriados
   nacionais oficiais. Resultado fica em `feriados_cache.json` por ano.
3. Calculo local (fallback) - fixos + moveis derivados da Pascoa pelo algoritmo
   de Meeus/Jones/Butcher. Usado quando a API falha e nao ha cache.

O fallback e essencial: o monitor roda por timer sem ninguem olhando. Uma falha
de rede nao pode virar falso positivo de falta.

Uso:
    import feriados
    feriados.eh_feriado(date(2026, 9, 7))   -> "Independencia do Brasil"
    feriados.eh_feriado(date(2026, 9, 8))   -> None

CLI (inspecao):
    python feriados.py 2026
"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
EXTRA_FILE = ROOT / "feriados_extra.json"
CACHE_FILE = ROOT / "feriados_cache.json"

API_URL = "https://brasilapi.com.br/api/feriados/v1/{ano}"
API_TIMEOUT = 15
# Feriado nacional muda por lei, nao por estacao: revalida no maximo a cada N dias.
CACHE_MAX_DAYS = 90

# Feriados nacionais de data fixa (mes, dia) -> nome
_FIXOS = {
    (1, 1):   "Confraternizacao Universal",
    (4, 21):  "Tiradentes",
    (5, 1):   "Dia do Trabalho",
    (9, 7):   "Independencia do Brasil",
    (10, 12): "Nossa Senhora Aparecida",
    (11, 2):  "Finados",
    (11, 15): "Proclamacao da Republica",
    (12, 25): "Natal",
}
# Consciencia Negra virou feriado nacional pela Lei 14.759/2023 (a partir de 2024)
_CONSCIENCIA_NEGRA_DESDE = 2024

_mem: dict[int, dict[date, str]] = {}
_mem_extra: dict[date, str] | None = None


# ----------------------------------------------------------------------
# FALLBACK LOCAL
# ----------------------------------------------------------------------
def pascoa(ano: int) -> date:
    """Domingo de Pascoa (algoritmo de Meeus/Jones/Butcher, gregoriano)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(ano, mes, dia + 1)


def _calculados(ano: int) -> dict[date, str]:
    """Feriados nacionais calculados localmente (fixos + moveis da Pascoa)."""
    out = {date(ano, mes, dia): nome for (mes, dia), nome in _FIXOS.items()}
    if ano >= _CONSCIENCIA_NEGRA_DESDE:
        out[date(ano, 11, 20)] = "Consciencia Negra"
    p = pascoa(ano)
    out[p - timedelta(days=48)] = "Carnaval (segunda)"
    out[p - timedelta(days=47)] = "Carnaval (terca)"
    out[p - timedelta(days=2)]  = "Sexta-feira Santa"
    out[p + timedelta(days=60)] = "Corpus Christi"
    return out


# ----------------------------------------------------------------------
# CACHE
# ----------------------------------------------------------------------
def _ler_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def _gravar_cache(cache: dict):
    try:
        tmp = CACHE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE_FILE)
    except OSError as e:
        print(f"[feriados] nao gravou cache: {e}")


def _cache_valido(entry: dict) -> bool:
    try:
        buscado = datetime.fromisoformat(entry["buscado_em"])
    except (KeyError, ValueError, TypeError):
        return False
    return (datetime.now() - buscado) <= timedelta(days=CACHE_MAX_DAYS)


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
def _da_api(ano: int) -> dict[date, str] | None:
    """Consulta BrasilAPI. Retorna None se falhar (rede, HTTP, payload)."""
    try:
        import requests
        r = requests.get(API_URL.format(ano=ano), timeout=API_TIMEOUT)
        r.raise_for_status()
        dados = r.json()
        out = {}
        for item in dados:
            try:
                out[date.fromisoformat(item["date"])] = item.get("name") or "Feriado nacional"
            except (KeyError, ValueError, TypeError):
                continue
        return out or None
    except Exception as e:                       # rede/HTTP/parse: cai no fallback
        print(f"[feriados] API indisponivel para {ano} ({type(e).__name__}); usando fallback local.")
        return None


# ----------------------------------------------------------------------
# API PUBLICA
# ----------------------------------------------------------------------
def feriados_nacionais(ano: int) -> dict[date, str]:
    """Feriados nacionais do ano: cache -> API -> calculo local."""
    if ano in _mem:
        return _mem[ano]

    cache = _ler_cache()
    entry = cache.get(str(ano))
    if entry and _cache_valido(entry):
        out = {}
        for k, v in (entry.get("feriados") or {}).items():
            try:
                out[date.fromisoformat(k)] = v
            except ValueError:
                continue
        if out:
            _mem[ano] = out
            return out

    out = _da_api(ano)
    origem = "api"
    if out is None:
        # Cache vencido ainda e melhor que nada: feriado nacional raramente muda.
        if entry and (entry.get("feriados") or {}):
            out = {}
            for k, v in entry["feriados"].items():
                try:
                    out[date.fromisoformat(k)] = v
                except ValueError:
                    continue
            origem = "cache-vencido"
        else:
            out = _calculados(ano)
            origem = "calculo"

    if origem == "api":
        cache[str(ano)] = {
            "buscado_em": datetime.now().isoformat(timespec="seconds"),
            "feriados": {d.isoformat(): n for d, n in sorted(out.items())},
        }
        _gravar_cache(cache)

    _mem[ano] = out
    return out


def feriados_extra() -> dict[date, str]:
    """Feriados estaduais/municipais declarados em feriados_extra.json."""
    global _mem_extra
    if _mem_extra is not None:
        return _mem_extra
    out: dict[date, str] = {}
    if EXTRA_FILE.exists():
        try:
            bruto = json.loads(EXTRA_FILE.read_text(encoding="utf-8"))
            for k, v in (bruto or {}).items():
                try:
                    out[date.fromisoformat(k)] = str(v)
                except ValueError:
                    print(f"[feriados] data invalida em {EXTRA_FILE.name}: {k!r} (ignorada)")
        except (json.JSONDecodeError, OSError) as e:
            print(f"[feriados] falha ao ler {EXTRA_FILE.name}: {e} (ignorado)")
    _mem_extra = out
    return out


def eh_feriado(d: date) -> str | None:
    """Nome do feriado, ou None. Extra (local) tem precedencia sobre o nacional."""
    return feriados_extra().get(d) or feriados_nacionais(d.year).get(d)


if __name__ == "__main__":
    import sys
    ano = int(sys.argv[1]) if len(sys.argv) > 1 else date.today().year
    print(f"Pascoa {ano} (calculo local): {pascoa(ano)}")
    nac = feriados_nacionais(ano)
    ext = {d: n for d, n in feriados_extra().items() if d.year == ano}
    todos = dict(nac); todos.update(ext)
    SEM = ("seg", "ter", "qua", "qui", "sex", "sab", "dom")
    for d in sorted(todos):
        tag = " [extra]" if d in ext else ""
        print(f"  {d.strftime('%d/%m/%Y')} {SEM[d.weekday()]}  {todos[d]}{tag}")
    print(f"total: {len(todos)} ({len(nac)} nacionais, {len(ext)} extras)")
