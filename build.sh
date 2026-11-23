#!/usr/bin/env bash
# Render build script: runs on every deploy.
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

python manage.py collectstatic --no-input
python manage.py migrate --no-input

# Creates the first admin account once, from the DJANGO_SUPERUSER_* environment variables
# (skipped when they are not set, or when the account already exists).
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  python manage.py createsuperuser --no-input || echo "Admin account already exists, skipping."
fi
