#!/usr/bin/env bash
# Oracle Cloud Always Free (Ubuntu 22.04/24.04, ARM veya AMD) sunucusunda DMC botunu kurar ve başlatır.
# Sunucuda çalıştır:
#   curl -fsSL https://raw.githubusercontent.com/CoskunerBerke/AG_Bot/main/deploy/oracle_setup.sh | bash
# İlk çalıştırmada .env oluşturup durur; anahtarları girdikten sonra aynı komutu tekrar çalıştır.
set -euo pipefail

REPO="https://github.com/CoskunerBerke/AG_Bot.git"
DIR="$HOME/AG_Bot"

echo "==> Saat dilimi America/New_York"
sudo timedatectl set-timezone America/New_York || true

if [ ! -f /swapfile ]; then
  echo "==> 2 GB swap (1 GB RAM'li E2.1.Micro'da Docker build için)"
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Docker kuruluyor"
  sudo apt-get update -y
  sudo apt-get install -y docker.io docker-compose-v2 git
  sudo systemctl enable --now docker
fi

if [ -d "$DIR/.git" ]; then
  echo "==> Kod güncelleniyor"
  git -C "$DIR" fetch -q origin
  git -C "$DIR" reset -q --hard origin/main   # sadece git'teki dosyalar; .env ve state/ etkilenmez
else
  echo "==> Kod indiriliyor"
  git clone "$REPO" "$DIR"
fi
cd "$DIR"

if [ ! -f .env ] || ! grep -q '^ALPACA_API_KEY=..' .env; then
  cat > .env <<'EOF'
ALPACA_API_KEY=
ALPACA_API_SECRET=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
EOF
  chmod 600 .env
  echo
  echo "!! $DIR/.env oluşturuldu. Alpaca PAPER anahtarlarını gir:  nano $DIR/.env"
  echo "!! Sonra bu betiği tekrar çalıştır."
  exit 0
fi

if ! grep -q 'paper-api.alpaca.markets' .env; then
  echo "!! .env içinde ALPACA_BASE_URL paper değil — durduruldu." >&2
  exit 1
fi

mkdir -p logs state/b reports data .cache
PROFILES=""
if [ -f .env.b ] && grep -q '^ALPACA_API_KEY=..' .env.b; then      # Bot B: ikinci paper hesap, 10AM sadece izlenir
  if grep -q 'paper-api.alpaca.markets' .env.b; then
    PROFILES="--profile b"
  else
    echo "!! .env.b içinde ALPACA_BASE_URL paper değil — Bot B başlatılmadı." >&2
  fi
fi
echo "==> Docker imajı derleniyor ve bot(lar) başlatılıyor ${PROFILES:+(A + B)}"
sudo docker compose $PROFILES up -d --build
sudo docker image prune -f >/dev/null 2>&1 || true      # eski imajlar diski doldurmasın
sleep 5
sudo docker ps --format '{{.Names}}  {{.Status}}'
sudo docker compose logs --tail 10 nasdaq-bot
echo
echo "Bot çalışıyor. Canlı log:  tail -f $DIR/logs/dmc_live.log   (Bot B: $DIR/logs/dmc_live_b.log)"
