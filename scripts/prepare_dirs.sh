#!/usr/bin/env sh
# Docker konteyneri root olmayan kullanıcıyla (uid 10001) çalışır.
# Bind-mount edilen klasörlerin bu kullanıcıya ait olması gerekir.
set -eu
cd "$(dirname "$0")/.."
mkdir -p data logs backups
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo ".env oluşturuldu (izin 600). Değerleri düzenleyin."
fi
sudo_cmd=""
[ "$(id -u)" -ne 0 ] && sudo_cmd="sudo"
$sudo_cmd chown -R 10001:10001 data logs backups
echo "Klasörler hazır: data/ logs/ backups/ -> uid 10001"
