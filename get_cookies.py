import requests
import json

# Nukedown Docker URL
BASE_URL = 'http://localhost:5505'

def login_and_get_token(username, password):
    """Login to nukedown and get auth token"""
    login_url = f'{BASE_URL}/api/auth/login'

    data = {
        'username': username,
        'password': password
    }

    try:
        response = requests.post(login_url, json=data)
        print(f"Login response status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            token = result.get('token')
            print(f"✅ Login successful! Token: {token}")

            # Test the token by making an authenticated request
            headers = {'Authorization': f'Bearer {token}'}
            test_response = requests.get(f'{BASE_URL}/api/auth/download-path', headers=headers)
            if test_response.status_code == 200:
                print("✅ Token verification successful!")
                return token
            else:
                print(f"❌ Token verification failed: {test_response.status_code}")
                return None
        else:
            error_msg = response.json().get('message', 'Unknown error')
            print(f"❌ Login failed: {error_msg}")
            return None

    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to nukedown. Make sure it's running on http://localhost:5505")
        return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

def test_with_cookies(token):
    """Show how to use the token as a cookie"""
    print("\n--- Cookie Usage Instructions ---")
    print("To use this token in your browser:")
    print("1. Open browser developer tools (F12)")
    print("2. Go to Application/Storage > Cookies")
    print("3. Set domain to 'localhost' and path to '/'")
    print("4. Add cookie named 'auth_token' with value:")
    print(f"   {token}")
    print("\nOr use this curl command:")
    print(f'curl -H "Authorization: Bearer {token}" http://localhost:5505/api/auth/download-path')

if __name__ == '__main__':
    print("Nukedown Docker Authentication Helper")
    print("=" * 40)

    # Try known users
    users_to_try = [
        ('root', 'admin123'),  # Reset password
        ('root', 'password'),  # Common default
        ('root', 'admin'),
        ('root', 'root'),
        ('testuser', 'testpass'),  # From our earlier test
        ('testuser', 'password')
    ]

    token = None
    for username, password in users_to_try:
        print(f"\nTrying login: {username}")
        token = login_and_get_token(username, password)
        if token:
            break

    if not token:
        print("\n❌ Could not login with known credentials.")
        print("Please enter your nukedown credentials:")
        username = input("Username: ")
        password = input("Password: ")
        token = login_and_get_token(username, password)

    if token:
        test_with_cookies(token)
    else:
        print("\n❌ Authentication failed. Please check:")
        print("1. Nukedown is running: docker ps | findstr nukedown")
        print("2. Correct port: http://localhost:5505")
        print("3. Valid username/password")