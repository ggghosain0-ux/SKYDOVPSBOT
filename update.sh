#!/usr/bin/env bash
set -euo pipefail
RED="\e[31m"; GREEN="\e[32m"; CYAN="\e[36m"; NC="\e[0m"

INSTALL_DIR="/opt/vpsbot"
APP_NAME="vpsbot"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo -e "${RED}Run this script as root.${NC}"
  exit 1
fi

echo -e "${CYAN}Updating repository in ${INSTALL_DIR}${NC}"
if [ ! -d "$INSTALL_DIR/.git" ]; then
  echo -e "${RED}No git repository found at ${INSTALL_DIR}. Aborting.${NC}"
  exit 1
fi

cd "$INSTALL_DIR"
git fetch --all
git reset --hard origin/main

echo -e "${CYAN}Installing Node dependencies...${NC}"
if [ -f package.json ]; then
  npm install --production
fi

echo -e "${CYAN}Restarting PM2 process ${APP_NAME}...${NC}"
if pm2 pid "$APP_NAME" >/dev/null 2>&1; then
  pm2 restart "$APP_NAME"
else
  echo -e "${YELLOW}PM2 process ${APP_NAME} not found, attempting to start...${NC}"
  if [ -f ecosystem.config.js ]; then
    pm2 start ecosystem.config.js --env production
  elif [ -f package.json ] && grep -q '"start"' package.json; then
    pm2 start npm --name "$APP_NAME" -- start
  elif [ -f index.js ]; then
    pm2 start index.js --name "$APP_NAME"
  elif [ -f main.js ]; then
    pm2 start main.js --name "$APP_NAME"
  else
    echo -e "${RED}Could not start the application. Please start it manually.${NC}"
    exit 1
  fi
fi

pm2 save

echo -e "${GREEN}Update complete.${NC}"
pm2 ls
