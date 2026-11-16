#!/usr/bin/env bash
# Build Harbor Mutual Policy Desk. Output: app/bin/PolicyDesk.exe, app/bin/policydesk-cli.exe, app/bin/releases.json
#   Windows (Git Bash / MSYS / Cygwin): the C# compiler that ships with Windows (.NET Framework 4.x)
#   Linux / macOS: Mono's compiler (mcs or csc), if Mono is installed - for a look at the app only;
#   UI Automation, and so desktop test automation, needs Windows.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRCS=(Program.cs Domain.cs Releases.cs Forms.cs SelfTest.cs)
REFS=(System.dll System.Core.dll System.Drawing.dll System.Windows.Forms.dll System.Web.Extensions.dll)
mkdir -p "${HERE}/bin"

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    win="${WINDIR:-${SYSTEMROOT:-C:/Windows}}"
    csc=""
    for dir in Framework64 Framework; do
      candidate="$(cygpath -u "${win}" 2>/dev/null || echo "${win}")/Microsoft.NET/${dir}/v4.0.30319/csc.exe"
      if [[ -x "${candidate}" ]]; then csc="${candidate}"; break; fi
    done
    if [[ -z "${csc}" ]]; then
      echo "[build] .NET Framework 4.x C# compiler not found." >&2
      exit 1
    fi
    to_win() { cygpath -w "$1" 2>/dev/null || echo "$1"; }
    args=()
    for ref in "${REFS[@]}"; do args+=("/r:${ref}"); done
    for src in "${SRCS[@]}"; do args+=("$(to_win "${HERE}/${src}")"); done
    MSYS2_ARG_CONV_EXCL='*' "${csc}" /nologo /target:winexe /optimize+ /warnaserror+ "/out:$(to_win "${HERE}/bin/PolicyDesk.exe")" "${args[@]}"
    MSYS2_ARG_CONV_EXCL='*' "${csc}" /nologo /target:exe /optimize+ /warnaserror+ /define:CONSOLE "/out:$(to_win "${HERE}/bin/policydesk-cli.exe")" "${args[@]}"
    ;;
  *)
    compiler="$(command -v mcs || command -v csc || true)"
    if [[ -z "${compiler}" ]]; then
      echo "[build] Needs Windows, or Mono (mcs / csc) on Linux / macOS: https://www.mono-project.com/download/" >&2
      exit 1
    fi
    args=()
    for ref in "${REFS[@]}"; do args+=("-r:${ref}"); done
    for src in "${SRCS[@]}"; do args+=("${HERE}/${src}"); done
    "${compiler}" -nologo -target:winexe -optimize+ "-out:${HERE}/bin/PolicyDesk.exe" "${args[@]}"
    "${compiler}" -nologo -target:exe -optimize+ -define:CONSOLE "-out:${HERE}/bin/policydesk-cli.exe" "${args[@]}"
    ;;
esac
cp "${HERE}/releases.json" "${HERE}/bin/releases.json"
echo "[build] ${HERE}/bin/PolicyDesk.exe"
echo "[build] ${HERE}/bin/policydesk-cli.exe"
