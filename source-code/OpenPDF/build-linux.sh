#!/usr/bin/env bash
# Linux counterpart of build-windows.ps1: tests, one-file build, frozen self-test.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
# Pillow's Tk bridge is imported dynamically on Linux; PyInstaller misses it.
python3 -m PyInstaller --noconfirm --clean --onefile --windowed --name OpenPDF \
  --collect-all pymupdf --collect-all pdf2docx --collect-submodules fontTools.ttLib.tables \
  --hidden-import PIL._tkinter_finder \
  --add-data 'LICENSE:.' --add-data 'THIRD_PARTY.md:.' app.py
if ! command -v tesseract >/dev/null; then
  echo 'warning: Tesseract not installed: OCR needs tesseract-ocr on PATH at runtime.' >&2
fi
# The self-test opens Tk windows; use a virtual display when none is available.
run=(dist/OpenPDF --self-test)
if [ -z "${DISPLAY:-}" ] && command -v xvfb-run >/dev/null; then
  run=(xvfb-run -a -s '-screen 0 1600x1200x24' "${run[@]}")
fi
# --self-test sends its output and any traceback to smoke-output.log, so print
# that here: otherwise a failure shows only an exit code. An old log would be
# mistaken for this run's when the program dies before opening its own.
rm -f smoke-output.log
status=0
"${run[@]}" || status=$?
if [ -f smoke-output.log ]; then
  # UI_CAPTURE lines only list screenshots; grep exits 1 when nothing is left.
  grep -v '^UI_CAPTURE' smoke-output.log || true
else
  echo 'OpenPDF self-test: smoke-output.log was not written.' >&2
fi
if [ "$status" -ne 0 ]; then
  echo "OpenPDF self-test failed with exit code $status." >&2
  exit "$status"
fi
echo 'Built dist/OpenPDF'
