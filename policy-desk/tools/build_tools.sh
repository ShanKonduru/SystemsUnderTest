#!/usr/bin/env bash
# Build tools/bin/UiaReport.exe (native UIA3 report) and tools/bin/PolicyDeskSmoke.exe from Git Bash on Windows. UI Automation is Windows-only.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ;;
  *) echo "[build_tools] UI Automation tools need Windows (run from Git Bash on Windows)." >&2; exit 1 ;;
esac
cmd.exe //c "$(cygpath -w "${HERE}/build_tools.bat")"
