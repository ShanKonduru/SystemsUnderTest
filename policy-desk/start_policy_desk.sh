#!/usr/bin/env bash
# Build and launch Harbor Mutual Policy Desk. Every option except --no-build goes to the app:
#
#   ./start_policy_desk.sh                           build, then launch (release v1, data in policy-desk/data)
#   ./start_policy_desk.sh --reset --release v2      seed data, UI release v2
#   ./start_policy_desk.sh --no-build --help         the app's options
#
# Windows (Git Bash / MSYS / Cygwin) runs the exe directly; Linux / macOS run it with Mono, for a look at the app
# only (desktop test automation needs Windows). Sign in with any user name; password "harbor" by default.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXE="${HERE}/app/bin/PolicyDesk.exe"
build=1
args=()
for arg in "$@"; do
  if [[ "${arg}" == "--no-build" ]]; then build=0; else args+=("${arg}"); fi
done

if [[ "${build}" == 1 ]]; then
  bash "${HERE}/app/build.sh"
fi
if [[ ! -f "${EXE}" ]]; then
  echo "[start] ${EXE} not found - run without --no-build." >&2
  exit 1
fi

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) MSYS2_ARG_CONV_EXCL='*' "${EXE}" "${args[@]+"${args[@]}"}" & ;;
  *)
    if ! command -v mono >/dev/null 2>&1; then
      echo "[start] Needs Windows, or Mono on Linux / macOS: https://www.mono-project.com/download/" >&2
      exit 1
    fi
    mono "${EXE}" "${args[@]+"${args[@]}"}" &
    ;;
esac
disown 2>/dev/null || true
echo "[start] Policy Desk launched. Sign in with any user name; password: --password, \$POLICYDESK_PASSWORD or \"harbor\"."
