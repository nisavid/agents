#!/bin/sh

set -eu
umask 077

fail() {
    printf '%s\n' "$1" >&2
    exit 2
}

case "$0" in
    */*) wrapper_directory=${0%/*} ;;
    *) wrapper_directory=. ;;
esac
supervisor="$wrapper_directory/supervise_prepared_release_validation.py"

if [ "$#" -ne 3 ]; then
    fail 'usage: run_prepared_release_validation.sh source-stage PREPARED_PYTHON REPOSITORY'
fi

mode=$1
prepared_python=$2
repository=$3
if [ "$mode" != 'source-stage' ]; then
    fail 'later-release prepared validation is unavailable in this source-stage release'
fi
entrypoint='scripts/validate_public_release.py'

case "$prepared_python" in
    /*) ;;
    *) fail 'prepared Python must be an absolute executable file' ;;
esac
if [ ! -f "$prepared_python" ] || [ ! -x "$prepared_python" ]; then
    fail 'prepared Python must be an absolute executable file'
fi

case "$repository" in
    /*) ;;
    *) fail 'repository must be an absolute directory' ;;
esac
if [ ! -d "$repository" ]; then
    fail 'repository must be an absolute directory'
fi

entrypoint="$repository/$entrypoint"
if [ ! -f "$entrypoint" ]; then
    fail 'selected release entrypoint is missing'
fi
if [ ! -f "$supervisor" ]; then
    fail 'prepared release validation supervisor is missing'
fi

exec /usr/bin/env -i \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/usr/bin:/bin \
    TZ=UTC \
    "$prepared_python" -I -B "$supervisor" \
    "$mode" "$repository"
