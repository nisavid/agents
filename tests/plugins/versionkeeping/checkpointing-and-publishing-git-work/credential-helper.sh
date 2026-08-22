#!/bin/sh

if [ "$1" = get ]; then
  printf '%s\n' \
    "username=${VERSIONKEEPING_TEST_CREDENTIAL_USERNAME:?}" \
    "password=${VERSIONKEEPING_TEST_CREDENTIAL_SECRET:?}"
fi
