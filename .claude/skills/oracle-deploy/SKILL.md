---
name: oracle-deploy
description: Guia para provisionar VM Oracle Always Free e fazer deploy do smart_auto do zero. Use ao recriar a infraestrutura de cloud.
---

# Deploy Oracle Always Free

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
