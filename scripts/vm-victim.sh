#!/usr/bin/env bash
# VM victim on the Thor: a real Ubuntu 24.04 guest under KVM (QEMU), nginx installed by cloud-init, reachable only
# over ssh and its forwarded ports. The agent never sees docker for it: what it gets is a host on the network.
#
#   scripts/vm-victim.sh seed      write the cloud-init seed (ssh key for user "ops", nginx) and serve it to the guest
#   scripts/vm-victim.sh start     create the overlay disk on first run, boot under KVM, wait for ssh
#   scripts/vm-victim.sh status    pid, ssh, http
#   scripts/vm-victim.sh ssh CMD   run a command in the guest as "ops"
#   scripts/vm-victim.sh stop      power off (the disk keeps its state); "stop --fresh" also deletes the overlay
#
# One-time, needs sudo (not done by this script):  sudo apt-get install -y qemu-system-arm qemu-utils qemu-efi-aarch64
# Base image: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-arm64.img in $VM_DIR.
# Ports on 127.0.0.1 only: ssh $VM_SSH_PORT (2222), http $VM_HTTP_PORT (8881), seed $VM_SEED_PORT (8099).
set -euo pipefail
DIR="${VM_DIR:-$HOME/vm-victim}"; mkdir -p "$DIR"; cd "$DIR"
BASE="${VM_BASE:-noble-server-cloudimg-arm64.img}"; DISK=victim.qcow2
MEM="${VM_MEM:-2048}"; CPUS="${VM_CPUS:-2}"
SSH_PORT="${VM_SSH_PORT:-2222}"; HTTP_PORT="${VM_HTTP_PORT:-8881}"; SEED_PORT="${VM_SEED_PORT:-8099}"
KEY="${VM_KEY:-$HOME/.ssh/id_ed25519}"; USER_NAME=ops
SSH_OPTS=(-p "$SSH_PORT" -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile="$DIR/known_hosts" -o ConnectTimeout=5 -o LogLevel=ERROR)

seed() {
  mkdir -p seed
  cat > seed/meta-data <<META
instance-id: victim-vm-$(date +%s)
local-hostname: victim-vm
META
  cat > seed/user-data <<USER
#cloud-config
hostname: victim-vm
users:
  - name: $USER_NAME
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - $(cat "$KEY.pub")
package_update: true
packages: [nginx]
write_files:
  - path: /var/www/html/index.html
    content: "victim-vm ok\n"
runcmd:
  - systemctl enable --now nginx
USER
  : > seed/vendor-data
  if ! curl -sf -m 2 "http://127.0.0.1:$SEED_PORT/meta-data" >/dev/null; then
    setsid nohup python3 -m http.server "$SEED_PORT" --bind 127.0.0.1 --directory seed > seed.log 2>&1 < /dev/null &
    echo $! > seed.pid; sleep 1
  fi
  echo "seed served on 127.0.0.1:$SEED_PORT (guest reads it as http://10.0.2.2:$SEED_PORT/)"
}

efi_code() { ls /usr/share/AAVMF/AAVMF_CODE.fd /usr/share/qemu-efi-aarch64/QEMU_EFI.fd 2>/dev/null | head -1; }

start() {
  command -v qemu-system-aarch64 >/dev/null || { echo "qemu-system-aarch64 missing: sudo apt-get install -y qemu-system-arm qemu-utils qemu-efi-aarch64" >&2; exit 1; }
  [ -f "$BASE" ] || { echo "base image $DIR/$BASE missing" >&2; exit 1; }
  if [ -f qemu.pid ] && kill -0 "$(cat qemu.pid)" 2>/dev/null; then echo "already running (pid $(cat qemu.pid))"; else
    seed >/dev/null
    [ -f "$DISK" ] || qemu-img create -q -f qcow2 -F qcow2 -b "$BASE" "$DISK" 10G
    [ -f efivars.fd ] || truncate -s 64M efivars.fd
    CODE=$(efi_code); [ -n "$CODE" ] || { echo "UEFI firmware missing (qemu-efi-aarch64)" >&2; exit 1; }
    qemu-system-aarch64 -M virt -accel kvm -cpu host -smp "$CPUS" -m "$MEM" \
      -drive if=pflash,format=raw,file="$CODE",readonly=on -drive if=pflash,format=raw,file=efivars.fd \
      -drive file="$DISK",if=virtio,format=qcow2 \
      -netdev user,id=n0,hostfwd=tcp:127.0.0.1:$SSH_PORT-:22,hostfwd=tcp:127.0.0.1:$HTTP_PORT-:80 -device virtio-net-pci,netdev=n0 \
      -smbios "type=1,serial=ds=nocloud;s=http://10.0.2.2:$SEED_PORT/" \
      -display none -serial file:console.log -monitor none -daemonize -pidfile qemu.pid
    echo "booted (pid $(cat qemu.pid)); console in $DIR/console.log"
  fi
  for i in $(seq 1 "${VM_READY_TIMEOUT:-240}"); do
    if ssh "${SSH_OPTS[@]}" "$USER_NAME@127.0.0.1" true 2>/dev/null; then echo "ssh ready after ${i}s"; break; fi
    sleep 1
  done
  for i in $(seq 1 "${VM_HTTP_TIMEOUT:-300}"); do
    if curl -sf -m 2 "http://127.0.0.1:$HTTP_PORT/" >/dev/null; then echo "nginx answering on :$HTTP_PORT after ${i}s"; return 0; fi
    sleep 2
  done
  echo "WARN: nginx not answering yet (cloud-init may still be installing packages); check: $0 ssh cloud-init status" >&2
}

status() {
  if [ -f qemu.pid ] && kill -0 "$(cat qemu.pid)" 2>/dev/null; then echo "qemu: running (pid $(cat qemu.pid))"; else echo "qemu: not running"; fi
  ssh "${SSH_OPTS[@]}" "$USER_NAME@127.0.0.1" 'echo "ssh: ok ($(hostname), up $(cut -d" " -f1 /proc/uptime)s)"; systemctl is-active nginx | sed "s/^/nginx: /"' 2>/dev/null || echo "ssh: not reachable"
  printf "http :%s -> %s\n" "$HTTP_PORT" "$(curl -s -m 3 -o /dev/null -w %{http_code} "http://127.0.0.1:$HTTP_PORT/")"
}

stop() {
  if [ -f qemu.pid ]; then kill "$(cat qemu.pid)" 2>/dev/null && echo "stopped qemu"; rm -f qemu.pid; fi
  if [ -f seed.pid ]; then kill "$(cat seed.pid)" 2>/dev/null; rm -f seed.pid; fi
  [ "${1:-}" = "--fresh" ] && rm -f "$DISK" efivars.fd && echo "overlay disk removed (next start is a fresh guest)"
  true
}

case "${1:-status}" in
  seed) seed ;;
  start) start ;;
  status) status ;;
  stop) shift; stop "$@" ;;
  ssh) shift; exec ssh "${SSH_OPTS[@]}" "$USER_NAME@127.0.0.1" "$@" ;;
  *) echo "usage: $0 seed|start|status|ssh CMD|stop [--fresh]" >&2; exit 2 ;;
esac
