#!/usr/bin/env bash
set -euo pipefail

readonly ROOT="${LISTING_TRACKER_ROOT:-${HOME}/services/token-listing-tracker}"
readonly CONFIG="${HOME}/.config/token-listing-tracker.env"
readonly STATE_DIR="${HOME}/.local/state/token-listing-tracker"
readonly DB="${STATE_DIR}/listings.db"
readonly LOCK="${STATE_DIR}/poll.lock"

umask 077
mkdir -p "${STATE_DIR}"

if [[ ! -r "${CONFIG}" ]]; then
  printf 'configuration_error: missing %s\n' "${CONFIG}" >&2
  exit 2
fi

# Runtime-only routing configuration. The file is user-owned and mode 0600.
set -a
# shellcheck disable=SC1090
source "${CONFIG}"
set +a

if [[ -z "${LISTING_TRACKER_TARGET:-}" ]]; then
  printf 'configuration_error: LISTING_TRACKER_TARGET is empty\n' >&2
  exit 2
fi

exec 9>"${LOCK}"
if ! flock -n 9; then
  # A prior one-minute poll still owns the lock. It will finish or timeout.
  exit 0
fi

exec env \
  -u HTTPS_PROXY \
  -u HTTP_PROXY \
  -u ALL_PROXY \
  timeout --signal=TERM --kill-after=5s 50s \
  "${ROOT}/.venv/bin/python" -m listing_tracker.live poll \
  --db "${DB}" \
  --timeout 20 \
  --min-success-ratio 1.0
