"""Da permiso al bot sobre tu Google Calendar y Gmail. Se corre UNA vez, en tu computadora.

    pip install google-auth-oauthlib
    python3 scripts/google_auth.py ruta/a/client_secret.json

Abre el navegador, eliges tu cuenta y aceptas. Al final imprime tres líneas
para pegar en ~/ig-bot/.env del servidor. No las compartas con nadie.
"""
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
    "https://www.googleapis.com/auth/gmail.send",
]

path = sys.argv[1] if len(sys.argv) > 1 else "client_secret.json"
flow = InstalledAppFlow.from_client_secrets_file(path, SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
client = json.load(open(path))["installed"]

print("\nPega esto en ~/ig-bot/.env del servidor:\n")
print(f"GOOGLE_CLIENT_ID={client['client_id']}")
print(f"GOOGLE_CLIENT_SECRET={client['client_secret']}")
print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
