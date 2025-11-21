import hashlib
import sqlite3

def hash_password(password):
    """Hash password using SHA256 (same as auth.py)"""
    return hashlib.sha256(password.encode()).hexdigest()

def reset_password(username, new_password):
    """Reset password for user"""
    hashed = hash_password(new_password)

    conn = sqlite3.connect('config/nukedown.db')
    c = conn.cursor()
    c.execute('UPDATE users SET password_hash = ? WHERE username = ?', (hashed, username))
    conn.commit()
    conn.close()

    print(f"✅ Password reset for user '{username}' to '{new_password}'")

# Reset root password to something simple
reset_password('root', 'admin123')

print("\nNow you can login with:")
print("Username: root")
print("Password: admin123")