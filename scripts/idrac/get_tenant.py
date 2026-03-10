from azure.identity import InteractiveBrowserCredential
import json, base64

cred = InteractiveBrowserCredential()
token = cred.get_token('https://management.azure.com/.default')
parts = token.token.split('.')
payload = parts[1] + '=' * (4 - len(parts[1]) % 4)
claims = json.loads(base64.b64decode(payload))
print(f"Tenant ID: {claims.get('tid')}")
print(f"UPN: {claims.get('upn', claims.get('unique_name'))}")
