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
  if python manage.py shell -c "import os, sys; from django.contrib.auth import get_user_model; sys.exit(0 if get_user_model().objects.filter(username=os.environ['DJANGO_SUPERUSER_USERNAME']).exists() else 1)"; then
    echo "Admin account already exists, skipping."
  else
    python manage.py createsuperuser --no-input || echo "Could not create the admin account (see message above)."
  fi
fi
