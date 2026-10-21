#!/bin/bash
set -euo pipefail

image=$1
suite=$2

with_docker() {
    local port
    name=db-test-$$
    trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    docker run -d --rm -p 127.0.0.1::5432 -e POSTGRES_PASSWORD=test --name "$name" "$image" >/dev/null
    port=$(docker port "$name" 5432/tcp | head -1 | sed 's/.*://')
    printf 'postgres on %s' "$port"
    until docker exec "$name" pg_isready -h 127.0.0.1 -q >/dev/null 2>&1; do printf '.'; sleep 0.5; done
    echo ' ready'
    cd backend
    TEST_DATABASE_URL="postgres://postgres:test@127.0.0.1:$port/postgres" uv run --no-sync pytest "$suite"
}

server_bin() {
    if command -v initdb >/dev/null 2>&1 && command -v pg_ctl >/dev/null 2>&1; then
        dirname "$(command -v initdb)"
    elif command -v pg_config >/dev/null 2>&1 && [ -x "$(pg_config --bindir)/initdb" ]; then
        pg_config --bindir
    else
        echo "db-test: Docker does not answer, and initdb and pg_ctl are not on PATH or in pg_config --bindir. Start Docker, install the Postgres server, or set TEST_DATABASE_URL." >&2
        exit 1
    fi
}

with_local_server() {
    bin=$(server_bin)
    dir=$(mktemp -d /tmp/db-test.XXXXXX)
    trap '"$bin/pg_ctl" -D "$dir/data" -m immediate -w stop >/dev/null 2>&1 || true; rm -rf "$dir"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    "$bin/initdb" -D "$dir/data" -U postgres --auth=trust -E UTF8 --locale=C >"$dir/initdb.log" 2>&1 \
        || { cat "$dir/initdb.log" >&2; exit 1; }
    "$bin/pg_ctl" -D "$dir/data" -l "$dir/server.log" -w \
        -o "-c listen_addresses='' -c unix_socket_directories='$dir' -c fsync=off" start >/dev/null \
        || { cat "$dir/server.log" >&2; exit 1; }
    echo "$("$bin/postgres" --version) on socket $dir ready"
    cd backend
    TEST_DATABASE_URL="postgresql://postgres@/postgres?host=$dir" uv run --no-sync pytest "$suite"
}

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    with_docker
else
    with_local_server
fi
