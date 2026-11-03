#!/bin/sh
# กู้คืนจากไฟล์สำรอง (ข้อมูลปัจจุบันจะถูกแทนที่)   tools/restore.sh ~/ccid4-backup/summons_20260209_0200.dump
set -e
cd "$(dirname "$0")/.."
file=$1
[ -f "$file" ] || { echo "ใช้: tools/restore.sh <ไฟล์ .dump>" >&2; exit 1; }
user=$(sed -n 's/^POSTGRES_USER=//p' .env | tr -d '\r'); user=${user:-summons}
db=$(sed -n 's/^POSTGRES_DB=//p' .env | tr -d '\r'); db=${db:-summons}
printf "ข้อมูลปัจจุบันจะถูกแทนที่ด้วย %s พิมพ์ YES เพื่อยืนยัน: " "$file"
read -r answer
[ "$answer" = "YES" ] || { echo "ยกเลิก"; exit 1; }
docker compose stop web worker
docker compose exec -T db pg_restore -U "$user" -d "$db" --clean --if-exists < "$file"
docker compose start web worker
files="${file%.dump}_files.tgz"
if [ -f "$files" ]; then
  docker compose exec -T web tar -xzf - -C /app/media < "$files"
  echo "กู้คืนไฟล์แนบการโทรจาก $files แล้ว"
else
  echo "ไม่พบไฟล์แนบสำรอง $files (กู้คืนเฉพาะฐานข้อมูล)"
fi
echo "กู้คืนเรียบร้อย"
