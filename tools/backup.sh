#!/bin/sh
# สำรองฐานข้อมูล + ไฟล์แนบการโทร (macOS / Linux) - ใช้กับ cron ได้ เช่น  0 2 * * * /opt/ccid4/tools/backup.sh
#   tools/backup.sh [โฟลเดอร์ปลายทาง] [เก็บกี่วัน]
set -e
cd "$(dirname "$0")/.."
dest=${1:-$HOME/ccid4-backup}
keep=${2:-30}
user=$(sed -n 's/^POSTGRES_USER=//p' .env | tr -d '\r'); user=${user:-summons}
db=$(sed -n 's/^POSTGRES_DB=//p' .env | tr -d '\r'); db=${db:-summons}
mkdir -p "$dest"
chmod 700 "$dest"
stamp=$(date +%Y%m%d_%H%M)
docker compose exec -T db pg_dump -U "$user" -Fc "$db" > "$dest/summons_$stamp.dump"
docker compose exec -T web sh -c "mkdir -p /app/media/call_attachments && tar -czf - -C /app/media call_attachments" \
  > "$dest/summons_${stamp}_files.tgz"
echo "สำรองแล้ว: $dest/summons_$stamp.dump และ $dest/summons_${stamp}_files.tgz"
find "$dest" -name 'summons_*' -mtime +"$keep" -delete
