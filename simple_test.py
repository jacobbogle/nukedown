import requests

response = requests.get('http://localhost:5100/')
print(f"Status: {response.status_code}")
print(f"Content: {response.text[:200]}")