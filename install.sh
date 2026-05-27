#!/usr/bin/env bash
set -euo pipefail
trap 'echo -e "${RED}An error occurred. Aborting.${NC}"; exit 1' ERR

# Colors
RED="\e[31m"
GREEN="\e[32m"
YELLOW="\e[33m"
BLUE="\e[34m"
CYAN="\e[36m"
NC="\e[0m"

REPO_URL="https://github.com/ggghosain0-ux/SKYDOVPSBOT.git"
INSTALL_DIR="/opt/skydovpsbot"
APP_NAME="skydovpsbot"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo -e "${RED}This installer must be run as root. Exiting.${NC}"
  exit 1
fi

echo -e "${CYAN}Installing to ${INSTALL_DIR}${NC}"

# Preserve .env if present and clean directory except .env
mkdir -p "$INSTALL_DIR"
echo -e "${YELLOW}Cleaning existing installation (preserving .env if present)...${NC}"
shopt -s dotglob nullglob
for item in "$INSTALL_DIR"/* "$INSTALL_DIR"/.[!.]* "$INSTALL_DIR"/?*; do
  [ -e "$item" ] || continue
  base=$(basename "$item")
  if [ "$base" = ".env" ]; then
    echo "  keeping .env"
    continue
  fi
  rm -rf "$item"
done

echo -e "${CYAN}Updating system packages...${NC}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y

echo -e "${CYAN}Installing required packages (git, curl, unzip, build tools)...${NC}"
apt-get install -y git curl unzip ca-certificates build-essential

echo -e "${CYAN}Installing Node.js (18.x) and npm via NodeSource...${NC}"
curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
apt-get install -y nodejs

echo -e "${CYAN}Verifying Node.js and npm installation...${NC}"
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo -e "${RED}Node.js or npm not found after installation. Exiting.${NC}"
  exit 1
fi

echo -e "${CYAN}Installing PM2 globally...${NC}"
npm install -g pm2

echo -e "${CYAN}Cloning repository...${NC}"
git clone "$REPO_URL" "$INSTALL_DIR" --depth=1 || { echo -e "${YELLOW}Clone failed, attempting to update existing repo...${NC}"; (cd "$INSTALL_DIR" && git fetch --all && git reset --hard origin/main); }

cd "$INSTALL_DIR"

echo -e "${CYAN}Installing Node dependencies...${NC}"
if [ -f package.json ]; then
  npm install --production
fi

echo -e "${CYAN}Creating logs directory...${NC}"
mkdir -p "$INSTALL_DIR/logs"

# Prompt user for secrets
echo -e "${GREEN}Please enter the following values for your bot. These will be saved into ${INSTALL_DIR}/.env${NC}"
read -r -p "Bot token: " BOT_TOKEN
read -r -p "Client ID: " CLIENT_ID
read -r -p "Database URL: " DATABASE_URL

cat > "$INSTALL_DIR/.env" <<EOF
BOT_TOKEN="$BOT_TOKEN"
CLIENT_ID="$CLIENT_ID"
DATABASE_URL="$DATABASE_URL"
EOF

echo -e "${CYAN}Starting the bot with PM2...${NC}"
if [ -f ecosystem.config.js ]; then
  pm2 start ecosystem.config.js --env production
elif [ -f package.json ] && grep -q '"start"' package.json; then
  pm2 start npm --name "$APP_NAME" -- start
elif [ -f index.js ]; then
  pm2 start index.js --name "$APP_NAME"
elif [ -f main.js ]; then
  pm2 start main.js --name "$APP_NAME"
else
  echo -e "${YELLOW}Could not determine start command. You may need to start the app manually with PM2.${NC}"
fi

pm2 save
echo -e "${CYAN}Configuring PM2 to start on boot...${NC}"
pm2 startup systemd -u root --hp /root

echo -e "${GREEN}Installation complete.${NC}"

echo -e "${BLUE}--- Bot Status ---${NC}"
pm2 status "$APP_NAME" || true

echo -e "${BLUE}--- PM2 Process List ---${NC}"
pm2 ls || true

echo -e "${GREEN}Useful commands:${NC}"
echo -e "  pm2 logs $APP_NAME"
echo -e "  pm2 restart $APP_NAME"
echo -e "  pm2 stop $APP_NAME"
echo -e "  pm2 delete $APP_NAME"
wget https://github.com/ggghosain0-ux/SKYDOVPSBOT/blob/main/dockerbot/install.py
python3 install.py
