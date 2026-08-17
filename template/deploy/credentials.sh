#!/bin/sh
# Gives the application role a password. Kept out of roles.sql on purpose: that file is
# committed, and a secret in a committed file is a secret in version control.
#
# The password goes in as a psql variable and is expanded with :'name', which quotes it as a
# SQL literal. Interpolating it into the SQL directly would let a password containing a single
# quote end the statement and start another -- and this runs as the superuser, so "it is only
# your own password" is not a reassuring bound on what that could do.
#
# Read from stdin rather than passed with -c, because psql does **not** expand variables in a
# -c string: `:'app_password'` arrives at the server literally and fails with a syntax error
# at the colon. The heredoc delimiter is quoted so the shell leaves the SQL alone and psql is
# the only thing doing any substitution.
set -eu

psql -v ON_ERROR_STOP=1 \
	--username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
	-v app_password="$APP_DB_PASSWORD" <<'SQL'
ALTER ROLE app_app PASSWORD :'app_password';
SQL
