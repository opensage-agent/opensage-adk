#!/usr/bin/env bash
# Setup script for Ray head node (run as root on a fresh Ubuntu 22.04 GCP VM).
#
# Prerequisites:
#   - Ubuntu 22.04 LTS
#   - A second disk attached (for btrfs), appears as /dev/sdb or /dev/disk/by-id/google-*
#   - GITHUB_TOKEN env var set (repo is private, needs a PAT with repo read access)
#
# Usage:
#   sudo GITHUB_TOKEN=ghp_xxx bash ray/setup_head.sh [BTRFS_DISK]
#
# BTRFS_DISK defaults to /dev/sdb. Override if your disk device differs.

set -euo pipefail

BTRFS_DISK="${1:-/dev/sdb}"
BTRFS_MOUNT="/data"
AIGISE_BRANCH="agentdocker-lite"
REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME="$(eval echo ~"$REAL_USER")"
UV="$REAL_HOME/.local/bin/uv"
VENV_DIR="$REAL_HOME/venv"
AIGISE_DIR="$REAL_HOME/aigise"

# --- GitHub token ---
if [ -z "${GITHUB_TOKEN:-}" ]; then
    echo "ERROR: GITHUB_TOKEN env var is required (repo is private)."
    echo "Usage: sudo GITHUB_TOKEN=ghp_xxx bash $0 [BTRFS_DISK]"
    exit 1
fi
AIGISE_REPO="https://${GITHUB_TOKEN}@github.com/opensage-agent/AIgiSE.git"

echo "=== [1/7] System packages ==="
apt-get update -qq
apt-get install -y -qq docker.io btrfs-progs git curl > /dev/null
usermod -aG docker "$REAL_USER" 2>/dev/null || true
systemctl enable --now docker

echo "=== [2/7] Format and mount btrfs data disk ==="
if mountpoint -q "$BTRFS_MOUNT"; then
    echo "  $BTRFS_MOUNT already mounted, skipping"
else
    if [ ! -b "$BTRFS_DISK" ]; then
        echo "ERROR: Disk $BTRFS_DISK not found. Pass the correct device as argument."
        echo "Available disks:"
        lsblk
        exit 1
    fi
    mkfs.btrfs -f "$BTRFS_DISK"
    mkdir -p "$BTRFS_MOUNT"
    mount "$BTRFS_DISK" "$BTRFS_MOUNT"
    grep -q "$BTRFS_DISK" /etc/fstab || \
        echo "$BTRFS_DISK $BTRFS_MOUNT btrfs defaults 0 0" >> /etc/fstab
    chmod 777 "$BTRFS_MOUNT"
fi
mkdir -p /data/rootfs_cache /data/aigise_ns
chmod -R 777 /data

echo "=== [3/7] Python (uv) ==="
if [ ! -x "$UV" ]; then
    su - "$REAL_USER" -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
fi
su - "$REAL_USER" -c "$UV python install 3.12 2>/dev/null || true"

echo "=== [4/7] Create venv + install Ray ==="
if [ ! -d "$VENV_DIR" ]; then
    su - "$REAL_USER" -c "$UV venv $VENV_DIR --python 3.12"
fi
su - "$REAL_USER" -c "$UV pip install --python $VENV_DIR/bin/python 'ray[default]>=2.9' --quiet"
echo "  Ray version: $($VENV_DIR/bin/python -c 'import ray; print(ray.__version__)')"

echo "=== [5/7] Clone AIgiSE ==="
if [ -d "$AIGISE_DIR" ]; then
    cd "$AIGISE_DIR"
    git fetch origin
    git checkout "$AIGISE_BRANCH"
    git pull origin "$AIGISE_BRANCH"
else
    su - "$REAL_USER" -c "git clone -b $AIGISE_BRANCH $AIGISE_REPO $AIGISE_DIR"
fi

echo "=== [6/7] Install AIgiSE ==="
su - "$REAL_USER" -c "cd $AIGISE_DIR && $UV pip install --python $VENV_DIR/bin/python -e . --quiet"
echo "  AIgiSE: $($VENV_DIR/bin/python -c 'import aigise; print("OK")')"

echo "=== [7/7] Start Ray head ==="
su - "$REAL_USER" -c "source $VENV_DIR/bin/activate && ray stop --force 2>/dev/null; ray start --head --port=6379 --dashboard-host=0.0.0.0"

echo ""
echo "=== Setup complete ==="
echo "  btrfs:     $BTRFS_MOUNT ($(df -h $BTRFS_MOUNT | tail -1 | awk '{print $2}'))"
echo "  venv:      $VENV_DIR"
echo "  AIgiSE:    $AIGISE_DIR (branch: $AIGISE_BRANCH)"
echo "  Ray head:  $(hostname -I | awk '{print $1}'):6379"
echo "  Dashboard: http://$(curl -s ifconfig.me):8265"
echo ""
echo "To activate the environment:"
echo "  source $VENV_DIR/bin/activate"
echo ""
echo "Next: run 'ray status' to verify, then see ray/quick_start.md"
