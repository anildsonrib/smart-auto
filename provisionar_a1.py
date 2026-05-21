"""
Tenta provisionar uma VM ARM Ampere A1 Always Free (4 OCPU + 24 GB) em loop ate conseguir.

Salvaguardas:
- Antes de cada tentativa, lista A1 existentes; se ja existe uma, para imediatamente.
- Specs dentro do Always Free (4 OCPU + 24 GB total = limite da tier).
- Intervalo de 5 min entre tentativas (sem rate-limit OCI).
- Notifica via Telegram bot em todos os estados relevantes (start, sucesso, erro inesperado).
- 'Out of capacity' nao gera cobranca; loop continua silenciosamente.
- Qualquer outro erro -> para e notifica para evitar custos acidentais.
"""
import os
import subprocess
import sys
import time
import oci
import requests
from pathlib import Path

# ---- config ----
TARGET_OCPUS   = 4
TARGET_RAM_GB  = 24
SHAPE          = "VM.Standard.A1.Flex"
AD             = "iLGc:SA-SAOPAULO-1-AD-1"
COMPARTMENT    = "ocid1.tenancy.oc1..REDACTED"
SUBNET         = "ocid1.subnet.oc1..REDACTED"
DISPLAY_NAME   = "smart-auto-a1"
SSH_PUB_FILE   = Path.home() / ".ssh" / "oracle_a1.pub"
INTERVAL_S     = 60   # 1 min (OCI rate-limit ~10/min em LaunchInstance, 1/min e seguro)
FAULT_DOMAINS  = ["FAULT-DOMAIN-1", "FAULT-DOMAIN-2", "FAULT-DOMAIN-3"]

ENV = Path(os.environ.get("SMART_AUTO_ENV", "/home/ubuntu/smart_auto/.env"))
def env(k):
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == k:
                return value.strip().strip("\"'")
    return os.environ.get(k, "")

def env_any(*keys):
    for key in keys:
        value = env(key)
        if value:
            return value
    return ""

TG_TOKEN = env_any("CACA_A1_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
TG_CHAT_RAW = env_any("CACA_A1_TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID")
TG_CHAT  = int(TG_CHAT_RAW) if TG_CHAT_RAW else 0

def tg(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        print("[tg-skip] CACA_A1_TELEGRAM_BOT_TOKEN/CHAT_ID nao configurados.", flush=True)
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"},
                      timeout=10)
    except Exception as e:
        print(f"[tg-err] {e}", flush=True)

def log(msg):
    print(time.strftime("[%H:%M:%S]"), msg, flush=True)

SSH_KEY  = "/home/ubuntu/.ssh/oracle_a1"
SSH_OPTS = ["-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "LogLevel=ERROR"]
PROJ_DIR = Path("/home/ubuntu/smart_auto")

def ssh_run(ip, cmd, timeout=300):
    r = subprocess.run(["ssh"] + SSH_OPTS + [f"ubuntu@{ip}", cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")

def scp_to(ip, local, remote):
    r = subprocess.run(["scp"] + SSH_OPTS + [str(local), f"ubuntu@{ip}:{remote}"],
                       capture_output=True, text=True, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")

def aguardar_ssh(ip, max_s=600):
    log(f"  aguardando SSH em {ip}...")
    t0 = time.time()
    while time.time() - t0 < max_s:
        rc, _ = ssh_run(ip, "echo READY", timeout=15)
        if rc == 0:
            log(f"  SSH OK apos {int(time.time()-t0)}s")
            return True
        time.sleep(10)
    return False

def migrar(ip):
    """Replica o ambiente smart_auto na nova A1 e desliga o bot local."""
    if not aguardar_ssh(ip):
        tg(f"⚠️ A1 criada em `{ip}` mas SSH nao respondeu em 10min.\nMigracao manual necessaria.")
        return

    log("instalando dependencias na A1...")
    rc, out = ssh_run(ip,
        "sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
        "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip cron && "
        "rm -rf ~/venv && python3 -m venv ~/venv && "
        "~/venv/bin/pip install -q --upgrade pip pdfplumber requests && "
        "mkdir -p ~/smart_auto && "
        "sudo timedatectl set-timezone America/Sao_Paulo && echo OK",
        timeout=600)
    if rc != 0:
        tg(f"⚠️ Falha instalando deps na A1.\n```\n{out[-500:]}\n```")
        return

    log("copiando arquivos do projeto...")
    for f in ("folha_ponto.py", "monitor_ponto.py", "telegram_bot.py", ".env",
              "smart-auto-bot.service"):
        rc, out = scp_to(ip, PROJ_DIR / f, f"/home/ubuntu/smart_auto/{f}")
        if rc != 0:
            tg(f"⚠️ Falha copiando {f}: {out[-200:]}")
            return

    log("instalando systemd + cron...")
    rc, out = ssh_run(ip,
        "sudo cp ~/smart_auto/smart-auto-bot.service /etc/systemd/system/ && "
        "sudo systemctl daemon-reload && "
        "sudo systemctl enable smart-auto-bot && "
        "(crontab -l 2>/dev/null | grep -v monitor_ponto; "
        "echo '0 15 * * 5 cd /home/ubuntu/smart_auto && /home/ubuntu/venv/bin/python monitor_ponto.py >> ~/smart_auto/monitor.log 2>&1') | crontab - && "
        "echo OK")
    if rc != 0:
        tg(f"⚠️ Falha instalando systemd/cron na A1.\n```\n{out[-500:]}\n```")
        return

    log("parando bot local (E2)...")
    subprocess.run(["sudo", "systemctl", "stop", "smart-auto-bot"], check=False)
    subprocess.run(["sudo", "systemctl", "disable", "smart-auto-bot"], check=False)

    log("subindo bot na A1...")
    rc, out = ssh_run(ip, "sudo systemctl start smart-auto-bot && sleep 3 && sudo systemctl is-active smart-auto-bot")
    if rc != 0 or "active" not in out:
        tg(f"⚠️ Bot nao subiu na A1.\n```\n{out[-400:]}\n```\nReverta com: `sudo systemctl start smart-auto-bot` na E2.")
        return

    # Auto-desativa o servico de caca apos sucesso (evita re-disparo em reboot)
    subprocess.run(["sudo", "systemctl", "disable", "caca-a1"], check=False)

    tg(f"✅ *Migracao completa*\n\n"
       f"A1 (`{ip}`) assumiu como host principal.\n"
       f"Bot ativo, cron sex 15h instalado.\n"
       f"Servico `caca-a1` auto-desativado (nao re-dispara em reboot).\n"
       f"E2 antiga (`203.0.113.10`) continua RUNNING (sem bot) - voce decide se mantem ou termina depois.\n\n"
       f"SSH A1: `ssh -i ~/.ssh/oracle_a1 ubuntu@{ip}`")

def main():
    cfg = oci.config.from_file()
    compute = oci.core.ComputeClient(cfg)
    network = oci.core.VirtualNetworkClient(cfg)

    # 1. Descobre imagem Ubuntu 22.04 aarch64
    imgs = compute.list_images(
        compartment_id=COMPARTMENT,
        operating_system="Canonical Ubuntu",
        operating_system_version="22.04",
        shape=SHAPE,
        sort_by="TIMECREATED",
        sort_order="DESC",
        limit=1,
    ).data
    if not imgs:
        msg = "❌ Sem imagem Ubuntu 22.04 ARM disponivel."
        log(msg); tg(msg); return
    img_id = imgs[0].id
    log(f"imagem: {imgs[0].display_name}")

    ssh_pub = SSH_PUB_FILE.read_text().strip()
    log(f"caca iniciada - alvo {TARGET_OCPUS} OCPU + {TARGET_RAM_GB} GB, intervalo {INTERVAL_S}s")

    tentativas = 0
    while True:
        tentativas += 1
        # Salvaguarda: ja existe uma A1?
        existing = [i for i in compute.list_instances(compartment_id=COMPARTMENT).data
                    if i.shape == SHAPE and i.lifecycle_state in ("RUNNING","PROVISIONING","STARTING")]
        if existing:
            inst = existing[0]
            msg = f"⚠️ Ja existe A1 ativa: `{inst.display_name}` ({inst.lifecycle_state}). Parando caca."
            log(msg); tg(msg); return

        fd = FAULT_DOMAINS[(tentativas - 1) % len(FAULT_DOMAINS)]
        log(f"tentativa #{tentativas} ({fd}): launching...")
        try:
            details = oci.core.models.LaunchInstanceDetails(
                availability_domain=AD,
                fault_domain=fd,
                compartment_id=COMPARTMENT,
                shape=SHAPE,
                shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                    ocpus=TARGET_OCPUS, memory_in_gbs=TARGET_RAM_GB),
                display_name=DISPLAY_NAME,
                source_details=oci.core.models.InstanceSourceViaImageDetails(
                    source_type="image", image_id=img_id),
                create_vnic_details=oci.core.models.CreateVnicDetails(
                    subnet_id=SUBNET, assign_public_ip=True),
                metadata={"ssh_authorized_keys": ssh_pub},
            )
            resp = compute.launch_instance(details)
            inst_id = resp.data.id
            log(f"✓ launch aceito! aguardando RUNNING...")
            # Aguarda RUNNING
            oci.wait_until(compute, compute.get_instance(inst_id),
                           "lifecycle_state", "RUNNING", max_wait_seconds=600)
            # Pega IP publico
            vnic_attaches = compute.list_vnic_attachments(
                compartment_id=COMPARTMENT, instance_id=inst_id).data
            ip = "?"
            if vnic_attaches:
                vnic = network.get_vnic(vnic_attaches[0].vnic_id).data
                ip = vnic.public_ip or "?"
            msg = (f"🎉 *A1 conquistada!* ({tentativas} tentativas)\n\n"
                   f"Nome: `{DISPLAY_NAME}`\nIP: `{ip}`\n\n"
                   f"_Iniciando migracao automatica..._")
            log(msg); tg(msg)
            migrar(ip)
            return

        except oci.exceptions.ServiceError as e:
            if e.status == 500 and "Out of host capacity" in (e.message or ""):
                log(f"  out of capacity ({fd}). aguardando {INTERVAL_S}s...")
                # Heartbeat: 1 ping a cada 500 tentativas pra confirmar vida sem encher chat
                if tentativas % 500 == 0:
                    tg(f"💓 caca-a1 vivo: {tentativas} tentativas, ainda sem capacidade.")
            elif e.status == 500 and "InternalError" in (e.code or ""):
                log(f"  internal error ({e.message}). aguardando {INTERVAL_S}s...")
            elif e.status == 429 or "TooManyRequests" in (e.code or ""):
                log(f"  rate-limit OCI ({e.code}). backoff 60s extra...")
                time.sleep(60)
            else:
                msg = (f"❌ *Caca A1 abortada* (erro inesperado)\n"
                       f"status={e.status} code={e.code}\n"
                       f"`{(e.message or '')[:300]}`")
                log(msg); tg(msg); return
        except Exception as e:
            msg = f"❌ *Caca A1 abortada*\n`{type(e).__name__}: {e}`"
            log(msg); tg(msg); return

        time.sleep(INTERVAL_S)

if __name__ == "__main__":
    main()
