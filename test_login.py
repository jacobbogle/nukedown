import requests

# Test login directly
url = 'http://localhost:5505/api/auth/login'
data = {'username': 'root', 'password': 'admin123'}

try:
    response = requests.post(url, json=data)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")

    if response.status_code == 200:
        token = response.json().get('token')
        print(f"\n✅ Success! Token: {token}")

        # Test token
        headers = {'Authorization': f'Bearer {token}'}
        test_response = requests.get('http://localhost:5505/api/auth/download-path', headers=headers)
        print(f"Token test: {test_response.status_code}")

except Exception as e:
    print(f"Error: {e}")