import requests
import os
from dotenv import load_dotenv

load_dotenv()

# GANTI PAKE KODE YANG LO COPY DARI URL BROWSER TADI
KODE_DARI_URL = "224607058840acce54df3c45f675187f843c9282"

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