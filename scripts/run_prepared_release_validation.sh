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

if [ "$#" -lt 3 ]; then
    fail 'usage: run_prepared_release_validation.sh MODE PREPARED_PYTHON REPOSITORY [ARGUMENT ...]'
fi

mode=$1
prepared_python=$2
repository=$3
shift 3

case "$mode" in
    public-release)
        entrypoint='scripts/validate_public_release.py'
        ;;
    phase7-production)
        entrypoint='scripts/run_phase7_production_integration.py'
        for argument in "$@"; do
            case "$argument" in
                --public-root | --public-root=*)
                    fail 'phase7-production receives --public-root from the entrypoint'
                    ;;
            esac
        done
        ;;
    *)
        fail 'mode must be public-release or phase7-production'
        ;;
esac

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
    "$mode" "$repository" "$@"
