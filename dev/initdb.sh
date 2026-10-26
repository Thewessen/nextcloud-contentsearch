#!/bin/sh
# Runs once on an empty database volume. Mirrors production: Nextcloud connects as
# a plain role `nextcloud` that owns the database `nextcloud`.
set -eu
psql -v ON_ERROR_STOP=1 -U postgres -v pw="$NC_DB_PASSWORD" <<'SQL'
CREATE ROLE nextcloud LOGIN PASSWORD :'pw';
CREATE DATABASE nextcloud OWNER nextcloud;
SQL
