#!/usr/bin/env bash
# UI Automation readiness report, both views (native UIA3 and NovaWindows), from Git Bash on Windows.
# Same options as tools/uia_report.bat:
#   tools/uia_report.sh -Exe app/bin/PolicyDesk.exe
#   tools/uia_report.sh -ProcessName PolicyDesk -Quiet
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ;;
  *) echo "[uia_report] UI Automation needs Windows (run from Git Bash on Windows)." >&2; exit 1 ;;
esac
[[ -f "${HERE}/bin/UiaReport.exe" ]] || bash "${HERE}/build_tools.sh"

# Windows programs get Windows paths for file arguments.
args=()
for arg in "$@"; do
  if [[ -e "${arg}" || "${arg}" == */* ]]; then args+=("$(cygpath -w "${arg}" 2>/dev/null || echo "${arg}")"); else args+=("${arg}"); fi
done

status=0
echo "==== Native UIA3 view =========================================================="
MSYS2_ARG_CONV_EXCL='*' "${HERE}/bin/UiaReport.exe" "${args[@]}" || status=$?
echo
echo "==== NovaWindows view =========================================================="
MSYS2_ARG_CONV_EXCL='*' powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "${HERE}/uia_report_nova.ps1")" \
  "${args[@]}" || { rc=$?; [[ ${status} -ne 0 ]] || status=${rc}; }
exit ${status}
