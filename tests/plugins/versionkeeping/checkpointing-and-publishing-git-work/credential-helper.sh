#!/bin/sh

requested_path=
while IFS='=' read -r key value; do
  if [ "$key" = path ]; then
    requested_path=$value
  fi
done

if [ "$1" = get ] &&
  [ "$requested_path" = "${VERSIONKEEPING_TEST_CREDENTIAL_PATH:?}" ]; then
  printf '%s\n' \
    "username=${VERSIONKEEPING_TEST_CREDENTIAL_USERNAME:?}" \
    "password=${VERSIONKEEPING_TEST_CREDENTIAL_SECRET:?}"
fi
