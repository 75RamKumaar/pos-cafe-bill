#!/usr/bin/env bash
set -e

echo "========================================================"
echo "              Cafe POS Production Server"
echo "========================================================"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 1. Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    source ".venv/bin/activate"
elif [ -f "venv/bin/activate" ]; then
    source "venv/bin/activate"
elif [ -f ".venv/Scripts/activate" ]; then
    source ".venv/Scripts/activate"
elif [ -f "venv/Scripts/activate" ]; then
    source "venv/Scripts/activate"
else
    echo "[ERROR] Virtual environment not found (.venv or venv)."
    echo "Please create one using: python3 -m venv .venv"
    exit 1
fi

echo ""
echo "[1/3] Applying database migrations..."
python manage.py migrate

echo ""
echo "[2/3] Collecting static assets..."
python manage.py collectstatic --noinput

echo ""
echo "[3/3] Starting Waitress WSGI production server on 0.0.0.0:8000..."
echo "--------------------------------------------------------"
echo "Local address:   http://127.0.0.1:8000"
echo "Network address: http://0.0.0.0:8000"
echo "Press Ctrl+C to terminate the server."
echo "--------------------------------------------------------"
echo ""

exec waitress-serve --listen=0.0.0.0:8000 cafe_pos.wsgi:application

