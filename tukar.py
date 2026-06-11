import requests
import os
from dotenv import load_dotenv

load_dotenv()

# Paste kode dari URL browser di sini, JANGAN di-commit ke Git.
# Setelah berhasil dapat refresh_token, kosongkan kembali baris ini.
KODE_DARI_URL = ""

if not KODE_DARI_URL:
    print("❌ KODE_DARI_URL masih kosong. Isi dulu sebelum dijalankan.")
    exit(1)

url = "https://www.strava.com/oauth/token"
payload = {
    'client_id': os.getenv("STRAVA_CLIENT_ID"),
    'client_secret': os.getenv("STRAVA_CLIENT_SECRET"),
    'code': KODE_DARI_URL,
    'grant_type': 'authorization_code'
}

response = requests.post(url, data=payload)
data = response.json()

print("\n" + "="*40)
if 'refresh_token' in data:
    print("🎉 SUKSES! Ini Refresh Token Master lo:")
    print(data['refresh_token'])
    print("="*40 + "\n")
    print("👉 COPY token di atas, lalu UPDATE nilai STRAVA_REFRESH_TOKEN di file .env lo!")
else:
    print("❌ Gagal. Kodenya udah expired atau salah copy.")
    print(data)