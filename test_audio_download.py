import requests
import time
import os

BASE_URL = 'http://localhost:5100'

# Test credentials - try common ones
test_users = [
    {'username': 'admin', 'password': 'admin'},
    {'username': 'user', 'password': 'password'},
    {'username': 'test', 'password': 'test'}
]

def login():
    # First try to create a test user
    from auth import AuthDB
    auth_db = AuthDB('config/nukedown.db')
    auth_db.create_user('testuser', 'testpass')
    print("Created test user")
    
    user = {'username': 'testuser', 'password': 'testpass'}
    response = requests.post(f'{BASE_URL}/api/auth/login', json=user)
    if response.status_code == 200:
        token = response.json()['token']
        print(f"Logged in as {user['username']}")
        return token
    print(f"Login failed: {response.status_code} - {response.text}")
    return None

def set_download_path(token):
    download_path = r'C:\Users\Bogle\Documents\Dev\boglefin\test_downloads'
    os.makedirs(download_path, exist_ok=True)
    headers = {'Authorization': f'Bearer {token}'}
    response = requests.post(f'{BASE_URL}/api/auth/download-path',
                           json={'download_path': download_path},
                           headers=headers)
    print(f"Set download path: {response.status_code} - {response.text}")

def set_media_path(token):
    media_path = r'C:\Users\Bogle\Documents\Dev\boglefin\test_media'
    os.makedirs(media_path, exist_ok=True)
    headers = {'Authorization': f'Bearer {token}'}
    response = requests.post(f'{BASE_URL}/api/auth/media-path',
                           json={'path_name': 'test_media', 'media_path': media_path},
                           headers=headers)
    print(f"Set media path: {response.status_code} - {response.text}")

def start_audio_download(token):
    # Test with the provided YouTube video
    url = 'https://www.youtube.com/watch?v=iiHmRezEdQ0'
    destination = r'C:\Users\Bogle\Documents\Dev\boglefin\test_media'

    headers = {'Authorization': f'Bearer {token}'}
    data = {
        'url': url,
        'destination_path': destination,
        'audio_only': True
    }
    response = requests.post(f'{BASE_URL}/api/youtube_download',
                           json=data,
                           headers=headers)
    print(f"Started download: {response.status_code} - {response.text}")
    return response.status_code == 200

def check_downloads(token):
    headers = {'Authorization': f'Bearer {token}'}
    response = requests.get(f'{BASE_URL}/api/youtube_downloads', headers=headers)
    if response.status_code == 200:
        downloads = response.json()
        print(f"Downloads: {downloads}")
        return downloads
    return []

def main():
    token = login()
    if not token:
        return

    set_download_path(token)
    set_media_path(token)

    if start_audio_download(token):
        print("Download started, waiting...")
        time.sleep(10)  # Wait a bit

        downloads = check_downloads(token)
        for download in downloads:
            print(f"Download status: {download.get('status')} - {download.get('title')}")

        # Wait longer for completion
        time.sleep(30)
        downloads = check_downloads(token)
        for download in downloads:
            print(f"Final status: {download.get('status')} - {download.get('title')}")

if __name__ == '__main__':
    main()