import hashlib
import sqlite3

def hash_password(password):
    """Hash password using SHA256 (same as auth.py)"""
    return hashlib.sha256(password.encode()).hexdigest()

def check_password(username, password):
    """Check if password matches for user"""
    hashed = hash_password(password)

    conn = sqlite3.connect('config/nukedown.db')
    c = conn.cursor()
    c.execute('SELECT password_hash FROM users WHERE username = ?', (username,))
    result = c.fetchone()
    conn.close()

    if result and result[0] == hashed:
        return True
    return False

def reset_password(username, new_password):
    """Reset password for user"""
    hashed = hash_password(new_password)

    conn = sqlite3.connect('config/nukedown.db')
    c = conn.cursor()
    c.execute('UPDATE users SET password_hash = ? WHERE username = ?', (hashed, username))
    conn.commit()
    conn.close()

    print(f"✅ Password reset for user '{username}'")

# Check common passwords
print("Checking common passwords for existing users...")

users = ['root', 'testuser']
common_passwords = ['password', 'admin', 'root', '123456', 'Slararis2020', 'dndadvoq']

for user in users:
    for pwd in common_passwords:
        if check_password(user, pwd):
            print(f"✅ Found credentials: {user} / {pwd}")
            break
    else:
        print(f"❌ No common password found for {user}")

print("\nTo reset a password, uncomment and modify these lines:")
print("# reset_password('root', 'newpassword')")
print("# reset_password('testuser', 'newpassword')")