#!/bin/sh
# Create .env for Docker with random secrets (macOS / Linux). Does nothing when .env already exists.
cd "$(dirname "$0")/.." || exit 1
[ -f .env ] && exit 0
secret() { LC_ALL=C tr -dc 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789' </dev/urandom | head -c "$1"; }
admin_pw=$(secret 12)
sed -e "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$(secret 60)|" \
    -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(secret 24)|" \
    -e "s|^DJANGO_SUPERUSER_PASSWORD=.*|DJANGO_SUPERUSER_PASSWORD=$admin_pw|" .env.example > .env
chmod 600 .env
mkdir -p data
printf 'admin / %s\n' "$admin_pw" > data/admin-password.txt
chmod 600 data/admin-password.txt
echo "Created .env  (administrator: admin / $admin_pw  - also saved in data/admin-password.txt)"
