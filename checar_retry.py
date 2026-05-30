"""
Wrapper de retry horario para falhas de conexao/download dos sistemas de ponto.

Nao contem loop proprio: cada execucao faz no maximo uma tentativa por sistema.
O agendamento horario fica a cargo do cron (etapa posterior, ainda nao aplicada).

Modos:
  python checar_retry.py --scheduled --system st|ss|eb
    - roda monitor_ponto.py --system X --no-state
    - exit 0  -> remove retry pendente de X
    - exit !=0 -> registra/atualiza retry_state.json so para X
      (a 1a falha agendada mantem o notificar_falha atual do monitor)

  python checar_retry.py --retry
    - para cada sistema pendente em retry_state.json:
      - se a entrada passou de RETRY_MAX_HOURS, remove e registra em stdout
      - senao roda monitor_ponto.py --system X --no-state --quiet-failure
        - exit 0  -> limpa X do retry_state.json (envio ja ocorreu no monitor)
        - exit !=0 -> incrementa attempts e atualiza last_error (silencioso)

Estado:
  retry_state.json (runtime, fora do git). Escrita atomica + lock local simples.
  Estrutura: {"st": {"since": ISO, "attempts": int, "last_error": str}, ...}

Nao afeta /checar, /forcar, justificativa.py nem o bot Telegram.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
MONITOR = ROOT / "monitor_ponto.py"
RETRY_STATE = ROOT / "retry_state.json"
LOCK = ROOT / ".retry_state.lock"

SYSTEMS = ("st", "ss", "eb")
RETRY_MAX_HOURS = 24

# Lock simples local: cria .retry_state.lock com O_EXCL. Considera stale apos N s.
LOCK_STALE_SECONDS = 60
LOCK_WAIT_SECONDS = 15


# ----------------------------------------------------------------------
# LOCK
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
                # quebra lock obsoleto (processo morto sem liberar)
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
    if not RETRY_STATE.exists():
        return {}
    try:
        return json.loads(RETRY_STATE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict):
    tmp = RETRY_STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    os.replace(str(tmp), str(RETRY_STATE))


# ----------------------------------------------------------------------
# MONITOR
# ----------------------------------------------------------------------
def _erro_curto(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    last = text.splitlines()[-1].strip()
    return last[:180] + ("..." if len(last) > 180 else "")


def run_monitor(system: str, quiet_failure: bool) -> tuple[int, str]:
    """Roda monitor_ponto.py --system X --no-state [--quiet-failure].

    Reemite stdout/stderr do monitor para os logs do cron e retorna
    (returncode, erro_curto) para registro em retry_state.
    """
    cmd = [sys.executable, str(MONITOR), "--system", system, "--no-state"]
    if quiet_failure:
        cmd.append("--quiet-failure")
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if res.stdout:
        sys.stdout.write(res.stdout)
    if res.stderr:
        sys.stderr.write(res.stderr)
    return res.returncode, _erro_curto(res.stderr or res.stdout)


# ----------------------------------------------------------------------
# MODOS
# ----------------------------------------------------------------------
def modo_scheduled(system: str) -> int:
    rc, err = run_monitor(system, quiet_failure=False)
    with _Lock(LOCK):
        state = load_state()
        if rc == 0:
            if system in state:
                del state[system]
                save_state(state)
                print(f"[retry] {system}: sucesso agendado, retry pendente removido.")
            else:
                print(f"[retry] {system}: sucesso agendado, sem retry pendente.")
        else:
            entry = state.get(system) or {}
            if entry:
                entry["attempts"] = int(entry.get("attempts", 1)) + 1
            else:
                entry = {"since": datetime.now().isoformat(timespec="seconds"), "attempts": 1}
            entry["last_error"] = err
            state[system] = entry
            save_state(state)
            print(f"[retry] {system}: falha agendada (exit {rc}), retry pendente registrado "
                  f"(attempts={entry['attempts']}).")
    return rc


def modo_retry() -> int:
    state = load_state()
    if not state:
        print("[retry] nenhum sistema pendente. No-op.")
        return 0

    agora = datetime.now()
    pendentes = [s for s in SYSTEMS if s in state]
    if not pendentes:
        print("[retry] retry_state.json sem sistemas validos. No-op.")
        return 0

    for system in pendentes:
        entry = state.get(system, {})
        since_raw = entry.get("since")
        expirado = False
        if since_raw:
            try:
                since = datetime.fromisoformat(since_raw)
                if agora - since > timedelta(hours=RETRY_MAX_HOURS):
                    expirado = True
            except ValueError:
                pass
        if expirado:
            with _Lock(LOCK):
                st = load_state()
                st.pop(system, None)
                save_state(st)
            print(f"[retry] {system}: entrada acima de {RETRY_MAX_HOURS}h (since={since_raw}), "
                  f"removida sem novo envio.")
            continue

        print(f"[retry] {system}: tentando novamente (since={since_raw}, "
              f"attempts={entry.get('attempts', '?')})...")
        rc, err = run_monitor(system, quiet_failure=True)
        with _Lock(LOCK):
            st = load_state()
            if rc == 0:
                st.pop(system, None)
                save_state(st)
                print(f"[retry] {system}: sucesso no retry, entrada removida.")
            else:
                cur = st.get(system) or entry or {
                    "since": agora.isoformat(timespec="seconds"), "attempts": 0}
                cur["attempts"] = int(cur.get("attempts", 0)) + 1
                cur["last_error"] = err
                cur.setdefault("since", agora.isoformat(timespec="seconds"))
                st[system] = cur
                save_state(st)
                print(f"[retry] {system}: ainda falhando (exit {rc}, "
                      f"attempts={cur['attempts']}), silencioso.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Retry horario de falhas de coleta de ponto.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scheduled", action="store_true",
                      help="execucao agendada de um sistema; registra retry em caso de falha")
    mode.add_argument("--retry", action="store_true",
                      help="reexecuta apenas os sistemas pendentes em retry_state.json")
    ap.add_argument("--system", choices=list(SYSTEMS),
                    help="sistema alvo (obrigatorio com --scheduled)")
    args = ap.parse_args()

    if args.scheduled:
        if not args.system:
            ap.error("--scheduled exige --system st|ss|eb")
        return modo_scheduled(args.system)
    return modo_retry()


if __name__ == "__main__":
    sys.exit(main())
