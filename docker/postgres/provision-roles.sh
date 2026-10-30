#!/bin/sh
# Creates or updates the training_owner and training_app roles in the db container.
# The image runs this on an empty volume. On an existing volume, or after a restore into a new cluster:
#   docker compose --env-file .env.production exec db sh /provision/provision-roles.sh
set -eu

psql --no-psqlrc --single-transaction --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set db_name="$POSTGRES_DB" \
  --set owner_password="$TRAINING_OWNER_DB_PASSWORD" \
  --set app_password="$TRAINING_APP_DB_PASSWORD" \
  --file /provision/provision-roles.sql
