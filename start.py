"""
Sassyroll Startup Orchestrator
================================
Menjalankan semua service sekaligus:
  1. ngrok  -> expose port 8000 ke internet (untuk Strava OAuth callback)
  2. FastAPI -> backend API (main.py)
  3. Bot     -> Telegram bot 24/7 (bot_server.py)

Cara pakai:
  python start.py
  python start.py --ngrok-domain your-static-domain.ngrok-free.app
"""

import os
import sys
import time
import signal
import subprocess
import argparse
from pathlib import Path
from dotenv import load_dotenv, set_key

# ── Pastikan encoding UTF-8 ──────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ENV_FILE = Path(__file__).parent / ".env"
VENV_BIN = Path(__file__).parent / "venv" / "Scripts"

def log(msg: str):
    print(f"[start.py] {msg}", flush=True)

# ── Parse argumen ────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Sassyroll startup orchestrator")
parser.add_argument(
    "--ngrok-domain",
    default=os.getenv("NGROK_STATIC_DOMAIN", ""),
    help="Static ngrok domain (misal: abc-xyz.ngrok-free.app). "
         "Bisa juga set env var NGROK_STATIC_DOMAIN.",
)
parser.add_argument(
    "--ngrok-token",
    default=os.getenv("NGROK_AUTHTOKEN", ""),
    help="Ngrok authtoken. Bisa juga set env var NGROK_AUTHTOKEN.",
)
parser.add_argument(
    "--skip-ngrok",
    action="store_true",
    help="Lewati ngrok (pakai BASE_URL yang sudah ada di .env).",
)
args = parser.parse_args()

load_dotenv(ENV_FILE)

processes: list[subprocess.Popen] = []

def shutdown(sig=None, frame=None):
    log("Mematikan semua service...")
    for p in processes:
        try:
            p.terminate()
        except Exception:
            pass
    # Hentikan ngrok jika aktif
    try:
        from pyngrok import ngrok as _ngrok
        _ngrok.kill()
    except Exception:
        pass
    log("Semua service dihentikan.")
    sys.exit(0)

signal.signal(signal.SIGINT,  shutdown)
signal.signal(signal.SIGTERM, shutdown)

# ── Step 1: Ngrok ────────────────────────────────────────────────────────────
public_url: str = ""

if args.skip_ngrok:
    public_url = os.getenv("BASE_URL", "http://localhost:8000")
    log(f"Melewati ngrok, pakai BASE_URL = {public_url}")
else:
    try:
        from pyngrok import ngrok, conf as ngrok_conf

        if args.ngrok_token:
            ngrok.set_auth_token(args.ngrok_token)
            log("Ngrok authtoken dikonfigurasi.")
        else:
            log("PERINGATAN: --ngrok-token tidak diberikan. Pastikan ngrok sudah login via 'ngrok config add-authtoken <token>'.")

        log("Memulai ngrok tunnel di port 8000...")
        if args.ngrok_domain:
            tunnel = ngrok.connect(8000, domain=args.ngrok_domain)
        else:
            tunnel = ngrok.connect(8000)

        public_url = tunnel.public_url
        # Pastikan HTTPS
        if public_url.startswith("http://"):
            public_url = public_url.replace("http://", "https://", 1)

        log(f"Ngrok aktif: {public_url}")

        # Simpan ke .env agar FastAPI & bot_server membacanya
        set_key(str(ENV_FILE), "BASE_URL", public_url)
        log(f"BASE_URL di .env diupdate: {public_url}")

    except Exception as e:
        log(f"ERROR saat start ngrok: {e}")
        log("Coba jalankan dengan --skip-ngrok jika tidak perlu Strava OAuth.")
        sys.exit(1)

# ── Step 2: FastAPI ──────────────────────────────────────────────────────────
log("Memulai FastAPI server (main.py) di port 8000...")
uvicorn_exe = VENV_BIN / "uvicorn.exe"
fastapi_proc = subprocess.Popen(
    [str(uvicorn_exe), "main:app", "--host", "0.0.0.0", "--port", "8000"],
    env={**os.environ, "PYTHONUTF8": "1", "BASE_URL": public_url},
)
processes.append(fastapi_proc)
time.sleep(3)  # beri waktu FastAPI inisialisasi

if fastapi_proc.poll() is not None:
    log("ERROR: FastAPI gagal start. Periksa error di atas.")
    shutdown()

log("FastAPI berjalan.")

# ── Step 3: Telegram Bot ─────────────────────────────────────────────────────
log("Memulai Telegram bot (bot_server.py)...")
python_exe = VENV_BIN / "python.exe"
bot_proc = subprocess.Popen(
    [str(python_exe), "bot_server.py"],
    env={**os.environ, "PYTHONUTF8": "1", "BASE_URL": public_url},
)
processes.append(bot_proc)
time.sleep(2)

if bot_proc.poll() is not None:
    log("ERROR: Bot server gagal start. Periksa error di atas.")
    shutdown()

log("Bot server berjalan.")

# ── Ringkasan ────────────────────────────────────────────────────────────────
print(flush=True)
print("=" * 55, flush=True)
print("  SASSYROLL SYSTEM AKTIF", flush=True)
print("=" * 55, flush=True)
print(f"  Ngrok URL : {public_url}", flush=True)
print(f"  Strava CB : {public_url}/strava/callback", flush=True)
print(f"  FastAPI   : http://localhost:8000/docs", flush=True)
print(f"  Bot       : berjalan di Telegram", flush=True)
print("=" * 55, flush=True)
print("  Tekan Ctrl+C untuk mematikan semua service.", flush=True)
print(flush=True)

# ── Keep alive: pantau proses ─────────────────────────────────────────────────
while True:
    time.sleep(5)
    for p in processes:
        if p.poll() is not None:
            log(f"PERINGATAN: Salah satu service berhenti (pid {p.pid}). Mematikan semua.")
            shutdown()
