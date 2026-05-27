#!/usr/bin/env bash
set -euo pipefail
RED="\e[31m"; GREEN="\e[32m"; CYAN="\e[36m"; NC="\e[0m"

INSTALL_DIR="/opt/vpsbot"
APP_NAME="vpsbot"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo -e "${RED}Run this script as root.${NC}"
  exit 1
fi

echo -e "${CYAN}Stopping and removing PM2 process ${APP_NAME}...${NC}"
pm2 delete "$APP_NAME" || true
pm2 save || true

echo -e "${CYAN}Removing PM2 startup hooks...${NC}"
pm2 unstartup systemd || true

echo -e "${CYAN}Removing installation directory ${INSTALL_DIR}...${NC}"
rm -rf "$INSTALL_DIR"

echo -e "${GREEN}Uninstall complete.${NC}"
