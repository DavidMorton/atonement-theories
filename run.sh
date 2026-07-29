#!/usr/bin/env bash
# Build the site and serve it locally at the same paths GitHub Pages uses.
#
#   ./run.sh            set up deps if needed, build, serve, open a browser
#   ./run.sh --watch    also rebuild whenever a source file changes
#   ./run.sh --no-open  don't launch a browser
#   PORT=9000 ./run.sh  serve somewhere other than 8000
#
# Everything it needs lives in a local .venv, so this never touches your
# system Python and never needs --break-system-packages.
#
# Written for bash 3.2, which is what macOS still ships: no arrays, no [[ ]],
# and no non-ASCII characters. In a non-UTF-8 locale bash 3.2 will happily
# swallow the bytes of a "..." or "-" into an adjacent variable name.
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8000}"
VENV=".venv"
SERVE_ROOT=".serve"
WATCH=0
OPEN=1

# bash 3.2 treats "$@" as unset rather than empty under `set -u`, so guard it.
if [ "$#" -gt 0 ]; then
  for arg in "$@"; do
    case "${arg}" in
      --watch)   WATCH=1 ;;
      --no-open) OPEN=0 ;;
      # Print the header comment, minus the '#', stopping at the first real
      # line of code — so this never drifts out of sync with the comment.
      -h|--help) awk 'NR>1 && /^[^#]/{exit} NR>1{sub(/^# ?/,""); print}' "$0"; exit 0 ;;
      *) echo "unknown option: ${arg} (try --help)" >&2; exit 2 ;;
    esac
  done
fi

# --- python ----------------------------------------------------------------

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install it (e.g. 'brew install python3') and try again." >&2
  exit 1
fi

# --- dependencies ----------------------------------------------------------
# The stamp file records which requirements.txt the venv was built from, so we
# only reinstall when that file actually changes.
STAMP="${VENV}/.requirements-stamp"

if [ ! -d "${VENV}" ]; then
  echo "creating ${VENV}..."
  python3 -m venv "${VENV}"
fi

PY="${VENV}/bin/python"

if [ ! -f "${STAMP}" ] || ! cmp -s requirements.txt "${STAMP}"; then
  echo "installing dependencies..."
  "${PY}" -m pip install --quiet --upgrade pip
  "${PY}" -m pip install --quiet -r requirements.txt
  cp requirements.txt "${STAMP}"
fi

# --- build -----------------------------------------------------------------
# site.yaml's baseurl is /atonement-theories, so the built site expects to live
# under that path. Copy _site into .serve/<baseurl>/ and serve from .serve, so
# local URLs match production exactly and nothing needs a separate local config.

BASEPATH="$("${PY}" -c 'import yaml;print((yaml.safe_load(open("site.yaml")).get("baseurl") or "").strip("/"))')"

build() {
  # Bail before touching what's being served. build.py clears _site/ up front,
  # so on failure that directory is half-written - the copy under .serve/ is
  # the last good one and must be left alone.
  "${PY}" build.py || return 1

  # Stage next to the live copy, then swap. A copy that's interrupted partway
  # leaves .serve/<basepath> untouched rather than half-replaced.
  local target staging previous
  target="${SERVE_ROOT}${BASEPATH:+/${BASEPATH}}"
  staging="${target}.staging.$$"
  previous="${target}.previous.$$"

  mkdir -p "$(dirname "${target}")"
  rm -rf "${staging}"
  cp -R _site "${staging}" || { rm -rf "${staging}"; return 1; }

  if [ -e "${target}" ]; then
    mv "${target}" "${previous}"
  fi
  mv "${staging}" "${target}"
  rm -rf "${previous}"
}

# Check the port before building - no point spending the build on a run that
# can't serve.
if command -v lsof >/dev/null 2>&1 && lsof -i ":${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port ${PORT} is already in use. Try: PORT=8001 $0" >&2
  exit 1
fi

build

# --- serve -----------------------------------------------------------------

"${PY}" -m http.server "${PORT}" --directory "${SERVE_ROOT}" >/dev/null 2>&1 &
SERVER_PID=$!

# Kill it whenever this script exits - normal exit, Ctrl-C, or error.
trap 'kill "${SERVER_PID}" 2>/dev/null || true; wait "${SERVER_PID}" 2>/dev/null || true' EXIT

URL="http://localhost:${PORT}/${BASEPATH:+${BASEPATH}/}"

# Wait for the port to actually accept connections before opening the browser.
TRIES=0
until curl -sf -o /dev/null "${URL}"; do
  # Bail out if the server died on startup.
  if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
    echo "server failed to start" >&2
    exit 1
  fi
  TRIES=$((TRIES + 1))
  if [ "${TRIES}" -gt 100 ]; then
    echo "server didn't come up after 10s" >&2
    exit 1
  fi
  sleep 0.1
done

if [ "${OPEN}" -eq 1 ]; then
  if command -v open >/dev/null 2>&1; then open "${URL}" || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "${URL}" >/dev/null 2>&1 || true
  fi
fi

echo
echo "  Serving ${URL}"
echo "  Ctrl-C to stop."
echo

# --- watch -----------------------------------------------------------------

if [ "${WATCH}" -eq 1 ]; then
  echo "  Watching content/ nodes/ _templates/ assets/ map/ *.yaml build.py"
  echo
  # No fswatch/entr dependency: hash a listing of the sources and poll it.
  # `ls -ld` output includes mtime and path, and works the same on BSD and GNU.
  fingerprint() {
    find content nodes _templates assets map site.yaml content.yaml build.py \
      -type f -exec ls -ld {} + 2>/dev/null | cksum
  }
  LAST="$(fingerprint)"
  while kill -0 "${SERVER_PID}" 2>/dev/null; do
    sleep 1
    NOW="$(fingerprint)"
    if [ "${NOW}" != "${LAST}" ]; then
      echo "  change detected - rebuilding"
      # A syntax error in a template shouldn't take the server down with it.
      build || echo "  build failed; keeping the last good copy"
      LAST="${NOW}"
    fi
  done
fi

# Block here until the server exits or you Ctrl-C.
wait "${SERVER_PID}"
