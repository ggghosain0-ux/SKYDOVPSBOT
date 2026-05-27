#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
SERVICE_NAME="skydovpsbot.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"

function is_root() {
  [ "$(id -u)" -eq 0 ]
}

function ensure_package_manager() {
  if command -v apt-get >/dev/null 2>&1; then
    echo "Detected apt package manager."
    return 0
  fi
  echo "Unsupported package manager. This installer currently supports Debian/Ubuntu systems with apt."
  exit 1
}

function install_system_packages() {
  echo "Installing system dependencies..."
  apt-get update
  apt-get install -y ca-certificates curl gnupg lsb-release python3 python3-venv python3-pip
}

function install_docker() {
  if command -v docker >/dev/null 2>&1; then
    echo "Docker is already installed."
    return
  fi

  echo "Installing Docker Engine..."
  mkdir -p /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
    $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

function install_python_deps() {
  echo "Creating Python virtual environment..."
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
}

function configure_docker_group() {
  local user="$SUDO_USER"
  if [ -z "$user" ] || [ "$user" = "root" ]; then
    user="$USER"
  fi
  if ! getent group docker >/dev/null 2>&1; then
    groupadd docker
  fi
  usermod -aG docker "$user"
  echo "Added user '$user' to docker group. Log out and back in for group membership to take effect."
}

function create_systemd_service() {
  echo "Creating systemd service $SERVICE_NAME..."
  cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=SKYDOVPSBOT Discord VPS deploy bot
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=${SUDO_USER:-$(whoami)}
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV_DIR/bin/python $SCRIPT_DIR/main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME"
  systemctl restart "$SERVICE_NAME"
  echo "Service $SERVICE_NAME enabled and started."
}

function main() {
  if ! is_root; then
    echo "This script requires root privileges to install system packages and create the service."
    echo "Run it with sudo: sudo bash $0"
    exit 1
  fi

  ensure_package_manager
  install_system_packages
  install_docker
  configure_docker_group
  install_python_deps
  mkdir -p "$PROJECT_ROOT/logs"
  create_systemd_service

  echo "Install complete."
  echo "Edit $SCRIPT_DIR/config.cfg to set your Discord bot token and admin IDs."
  echo "If you changed user group membership, log out and back in, then verify Docker access with 'docker ps'."
}

main
