"""
Authentication and User Management for nukedown
Handles user login, session management, and path storage
"""

import sqlite3
import hashlib
import os
from datetime import datetime, timedelta
import secrets

class AuthDB:
    """Handle user authentication and path management"""
    
    def __init__(self, db_path='config/nukedown.db'):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.init_db()
    
    def get_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_db(self):
        """Initialize database tables"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # User paths table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_paths (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                path_name TEXT NOT NULL,
                download_path TEXT NOT NULL,
                media_path TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                UNIQUE(user_id, path_name)
            )
        ''')
        
        # Sessions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        
        # Downloads table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                source TEXT DEFAULT 'unknown',
                url TEXT,
                manga_id TEXT,
                cover_url TEXT,
                temp_path TEXT,
                destination TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                progress INTEGER DEFAULT 0,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                error TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')

        # manga library table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS manga_library (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                library_name TEXT NOT NULL,
                title TEXT NOT NULL,
                full_path TEXT NOT NULL,
                cover_url TEXT,
                file_count INTEGER DEFAULT 0,
                last_scanned TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                UNIQUE(user_id, full_path)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    @staticmethod
    def _hash_password(password):
        """Hash password using SHA256"""
        return hashlib.sha256(password.encode()).hexdigest()
    
    def authenticate_user(self, username, password):
        """Authenticate user and return user_id if successful"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        password_hash = self._hash_password(password)
        cursor.execute(
            'SELECT id FROM users WHERE username = ? AND password_hash = ?',
            (username, password_hash)
        )
        result = cursor.fetchone()
        conn.close()
        
        return result['id'] if result else None
    
    def create_session(self, user_id, expires_in_hours=720):
        """Create session token for user (default 30 days)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=expires_in_hours)
        
        cursor.execute(
            'INSERT INTO sessions (user_id, token, expires_at) VALUES (?, ?, ?)',
            (user_id, token, expires_at)
        )
        conn.commit()
        conn.close()
        
        return token
    
    def verify_session(self, token):
        """Verify session token and return user_id"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT user_id FROM sessions WHERE token = ? AND expires_at > datetime("now")',
            (token,)
        )
        result = cursor.fetchone()
        conn.close()
        
        return result['user_id'] if result else None
    
    def invalidate_session(self, token):
        """Invalidate session token"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM sessions WHERE token = ?', (token,))
        conn.commit()
        conn.close()
    
    def save_path_config(self, user_id, path_name, download_path, media_path):
        """Save or update user's path configuration"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT id FROM user_paths WHERE user_id = ? AND path_name = ?',
            (user_id, path_name)
        )
        
        if cursor.fetchone():
            # Update existing
            cursor.execute(
                '''UPDATE user_paths 
                   SET download_path = ?, media_path = ? 
                   WHERE user_id = ? AND path_name = ?''',
                (download_path, media_path, user_id, path_name)
            )
        else:
            # Insert new
            cursor.execute(
                '''INSERT INTO user_paths (user_id, path_name, download_path, media_path)
                   VALUES (?, ?, ?, ?)''',
                (user_id, path_name, download_path, media_path)
            )
        
        conn.commit()
        conn.close()
    
    def get_user_paths(self, user_id):
        """Get all path configurations for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT path_name, download_path, media_path FROM user_paths WHERE user_id = ?',
            (user_id,)
        )
        results = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in results]
    
    def get_user_path(self, user_id, path_name):
        """Get specific path configuration for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT download_path, media_path FROM user_paths WHERE user_id = ? AND path_name = ?',
            (user_id, path_name)
        )
        result = cursor.fetchone()
        conn.close()
        
        return dict(result) if result else None
    
    def delete_user_path(self, user_id, path_name):
        """Delete path configuration for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'DELETE FROM user_paths WHERE user_id = ? AND path_name = ?',
            (user_id, path_name)
        )
        conn.commit()
        conn.close()
    
    def create_user(self, username, password):
        """Create new user (admin only)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        password_hash = self._hash_password(password)
        
        try:
            cursor.execute(
                'INSERT INTO users (username, password_hash) VALUES (?, ?)',
                (username, password_hash)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()
    
    def change_password(self, user_id, old_password, new_password):
        """Change user password"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Verify old password
        old_hash = self._hash_password(old_password)
        cursor.execute('SELECT id FROM users WHERE id = ? AND password_hash = ?', (user_id, old_hash))
        
        if not cursor.fetchone():
            conn.close()
            return False
        
        # Update to new password
        new_hash = self._hash_password(new_password)
        cursor.execute('UPDATE users SET password_hash = ? WHERE id = ?', (new_hash, user_id))
        conn.commit()
        conn.close()
        
        return True
    
    def set_download_path(self, user_id, download_path):
        """Set the global download path for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Normalize the path to use correct separators for current OS
        download_path = os.path.normpath(download_path)
        
        # Store download path as a special user path entry
        cursor.execute(
            '''INSERT OR REPLACE INTO user_paths (user_id, path_name, download_path, media_path)
               VALUES (?, ?, ?, ?)''',
            (user_id, '__download_path__', download_path, '')
        )
        conn.commit()
        conn.close()
    
    def get_download_path(self, user_id):
        """Get the global download path for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT download_path FROM user_paths WHERE user_id = ? AND path_name = ?',
            (user_id, '__download_path__')
        )
        result = cursor.fetchone()
        conn.close()
        
        path = result['download_path'] if result else None
        # Normalize the path to use correct separators for current OS
        return os.path.normpath(path) if path else None
    
    def delete_download_path(self, user_id):
        """Delete the global download path for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'DELETE FROM user_paths WHERE user_id = ? AND path_name = ?',
            (user_id, '__download_path__')
        )
        conn.commit()
        conn.close()
    
    def save_media_path(self, user_id, path_name, media_path):
        """Save media path for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Normalize the path to use correct separators for current OS
        media_path = os.path.normpath(media_path)
        
        cursor.execute(
            '''INSERT OR REPLACE INTO user_paths (user_id, path_name, download_path, media_path)
               VALUES (?, ?, ?, ?)''',
            (user_id, path_name, '', media_path)
        )
        conn.commit()
        conn.close()
    
    def get_media_paths(self, user_id):
        """Get all media paths for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT path_name, media_path FROM user_paths WHERE user_id = ? AND path_name != ?',
            (user_id, '__download_path__')
        )
        results = cursor.fetchall()
        conn.close()
        
        # Normalize paths to use correct separators for current OS
        return [{'path_name': row['path_name'], 'media_path': os.path.normpath(row['media_path'])} for row in results]
    
    def delete_media_path(self, user_id, path_name):
        """Delete media path for user"""
        if path_name == '__download_path__':
            return False  # Don't allow deleting the special download path entry
        
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'DELETE FROM user_paths WHERE user_id = ? AND path_name = ?',
            (user_id, path_name)
        )
        conn.commit()
        conn.close()
        return True
    
    # Downloads management methods
    def add_download(self, user_id, download_data):
        """Add a new download for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO downloads (user_id, title, source, url, manga_id, cover_url, temp_path, destination, status, progress, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_id,
            download_data.get('title', ''),
            download_data.get('source', 'unknown'),
            download_data.get('url', ''),
            download_data.get('manga_id', ''),
            download_data.get('cover_url', ''),
            download_data.get('temp_path', ''),
            download_data.get('destination', ''),
            download_data.get('status', 'pending'),
            download_data.get('progress', 0),
            download_data.get('error', '')
        ))
        
        download_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return download_id
    
    def get_user_downloads(self, user_id):
        """Get all downloads for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM downloads WHERE user_id = ? ORDER BY added_at DESC', (user_id,))
        downloads = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return downloads
    
    def update_download(self, download_id, user_id, update_data):
        """Update download for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Build update query dynamically
        set_parts = []
        values = []
        
        for key, value in update_data.items():
            if key in ['status', 'progress', 'error', 'completed_at']:
                set_parts.append(f'{key} = ?')
                values.append(value)
        
        if not set_parts:
            conn.close()
            return False
        
        query = f'UPDATE downloads SET {", ".join(set_parts)} WHERE id = ? AND user_id = ?'
        values.extend([download_id, user_id])
        
        cursor.execute(query, values)
        updated = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        return updated
    
    def delete_download(self, download_id, user_id):
        """Delete download for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM downloads WHERE id = ? AND user_id = ?', (download_id, user_id))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        return deleted
    
    def get_download_stats(self, user_id):
        """Get download statistics for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
            FROM downloads 
            WHERE user_id = ?
        ''', (user_id,))
        
        stats = dict(cursor.fetchone())
        conn.close()
        
        return stats
    
    def save_manga_entry(self, user_id, library_name, title, full_path, cover_url, file_count):
        """Save or update manga entry in library"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO manga_library (user_id, library_name, title, full_path, cover_url, file_count, last_scanned)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, full_path) DO UPDATE SET
                library_name = excluded.library_name,
                title = excluded.title,
                cover_url = excluded.cover_url,
                file_count = excluded.file_count,
                last_scanned = CURRENT_TIMESTAMP
        ''', (user_id, library_name, title, full_path, cover_url, file_count))
        
        conn.commit()
        conn.close()
    
    def get_manga_library(self, user_id):
        """Get all manga entries for user grouped by library"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT library_name, title, full_path, cover_url, file_count
            FROM manga_library
            WHERE user_id = ?
            ORDER BY library_name, title
        ''', (user_id,))
        
        libraries = {}
        for row in cursor.fetchall():
            library_name = row['library_name']
            if library_name not in libraries:
                libraries[library_name] = []
            
            libraries[library_name].append({
                'title': row['title'],
                'full_path': row['full_path'],
                'cover_url': row['cover_url'],
                'file_count': row['file_count']
            })
        
        conn.close()
        return libraries
    
    def delete_manga_entry(self, user_id, full_path):
        """Delete manga entry from library"""
        import os
        normalized_path = os.path.normpath(full_path)
        
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM manga_library WHERE user_id = ? AND full_path = ?', (user_id, normalized_path))
        deleted = cursor.rowcount > 0
        
        # Also try with the original path in case normalization differs
        if not deleted:
            cursor.execute('DELETE FROM manga_library WHERE user_id = ? AND full_path = ?', (user_id, full_path))
            deleted = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return deleted
    
    def delete_all_manga_entries(self, user_id):
        """Delete all manga entries for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM manga_library WHERE user_id = ?', (user_id,))
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        return deleted_count
    
    def delete_manga_entries_by_library(self, user_id, library_path):
        """Delete all manga entries for user in a specific library"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Normalize the library path for comparison
        normalized_library_path = library_path.replace('\\', '/').rstrip('/')
        
        # Delete entries that start with the library path
        cursor.execute('DELETE FROM manga_library WHERE user_id = ? AND full_path LIKE ?', (user_id, f"{normalized_library_path}/%"))
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        return deleted_count
    
    def get_all_manga_paths(self, user_id):
        """Get all manga paths for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT full_path FROM manga_library WHERE user_id = ?', (user_id,))
        rows = cursor.fetchall()
        conn.close()
        
        return [row[0] for row in rows]
