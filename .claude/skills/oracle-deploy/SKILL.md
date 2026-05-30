---
name: oracle-deploy
description: Guia para provisionar VM Oracle Always Free e fazer deploy do smart_auto do zero, incluindo o hunter A1 (caca-a1) para conseguir capacidade ARM. Use ao recriar a infraestrutura de cloud.
---

# Deploy Oracle Always Free

## Sequencia minima

1. Criar Compartment + VCN + subnet publica + Internet Gateway.
2. Gerar chave SSH dedicada **com passphrase vazia em PowerShell nativo** (`-N ""`, NAO `-N '""'` que vira passphrase literal):
   ```powershell
   ssh-keygen -t ed25519 -f $HOME\.ssh\oracle_a1 -N ""
   ```
3. Provisionar `VM.Standard.E2.1.Micro` (AMD, sempre disponivel) com `deploy/cloud-init.yaml`:
   ```yaml
   #cloud-config
   users:
     - name: ubuntu
       sudo: ALL=(ALL) NOPASSWD:ALL
       shell: /bin/bash
       ssh_authorized_keys:
         - ssh-ed25519 AAAA...
   runcmd:
     - apt-get update
     - apt-get install -y python3-venv git
     - sudo -u ubuntu python3 -m venv /home/ubuntu/venv
     - sudo -u ubuntu /home/ubuntu/venv/bin/pip install requests pdfplumber oci
   ```
4. `scp` dos arquivos do projeto + `.env` para `/home/ubuntu/smart_auto/`.
5. Instalar systemd units:
   ```bash
   sudo cp smart-auto-bot.service /etc/systemd/system/
   sudo systemctl enable --now smart-auto-bot
   ```
6. Crontab do `ubuntu` (checagens via wrapper `checar_retry.py`; `--scheduled` injeta `--no-state` e registra retry em falha, `--retry` reexecuta de hora em hora os sistemas pendentes):
   ```
   0 15 * * 5 cd /home/ubuntu/smart_auto && /home/ubuntu/venv/bin/python checar_retry.py --scheduled --system st
   0 21 * * 5 cd /home/ubuntu/smart_auto && /home/ubuntu/venv/bin/python checar_retry.py --scheduled --system ss
   0  6 * * 1 cd /home/ubuntu/smart_auto && /home/ubuntu/venv/bin/python checar_retry.py --scheduled --system eb
   30 * * * * cd /home/ubuntu/smart_auto && /home/ubuntu/venv/bin/python checar_retry.py --retry >> ~/smart_auto/retry.log 2>&1
   ```
   `retry_state.json` e `retry.log` sao runtime na VM, fora do git; nao copiar no deploy.

## Caca A1

Oracle nao garante capacidade ARM `VM.Standard.A1.Flex`. Sao Paulo (1 AD) e cronicamente cheio. Para conseguir sem ficar manualmente tentando, rodar `provisionar_a1.py` como servico:

```ini
# /etc/systemd/system/caca-a1.service
[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/smart_auto
ExecStart=/home/ubuntu/venv/bin/python /home/ubuntu/smart_auto/provisionar_a1.py
Restart=no
StandardOutput=append:/home/ubuntu/smart_auto/caca_a1.log
```

`Restart=no` e crucial: apos sucesso o script faz `systemctl disable caca-a1` e termina. Restart automatico tentaria provisionar uma segunda A1 -> custo.

## Garantias de custo zero

- Shape sempre `4 OCPU + 24 GB` (limite Always Free ARM).
- Pre-check: `ListInstances` filtrando A1 ativa; se ja existe, aborta.
- Auto-migracao apos sucesso: para bot na E2 (evita duplo polling), instala tudo na A1, derruba o servico de caca.
- Notificacoes silenciosas para "out of capacity"; so notifica em sucesso ou erro inesperado.

## Armadilhas

- `cloud-init` so injeta chave SSH se `users[].ssh_authorized_keys` estiver presente; metadata `ssh_keys` nao basta.
- API OCI exige `~/.oci/config` + `oci_api_key.pem`. Gerar pelo console > "API Keys".
- Sao Paulo so tem 1 AD - nao adianta iterar por AD list.
- Block volume Always Free: ate 200 GB total. Nao criar volumes extras sem checar.
