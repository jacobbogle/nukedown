#!/usr/bin/env python3
"""
nukedown - Simple manga Request Interface
A Jellyseerr-like interface for requesting manga downloads
Now using direct manga site integration for better results
"""

from asyncio.log import logger
import os
import subprocess
import json
import requests
import xml.etree.ElementTree as ET
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, jsonify, Response
from functools import lru_cache, wraps
import hashlib
import threading
import shutil
import urllib.parse
import yt_dlp

# Import authentication module
from auth import AuthDB

app = Flask(__name__, template_folder='templates')
auth_db = AuthDB('config/nukedown.db')


# Simple in-memory storage for requests (in production, use a database)
requests_db = []

# Path aliases storage (in production, use a database)
path_aliases = {}

def normalize_title(title):
    """Normalize title for deduplication"""
    import re
    # Remove special characters, convert to lowercase, strip whitespace
    normalized = re.sub(r'[^\w\s]', '', title.lower()).strip()
    # Remove common words that don't affect uniqueness
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}
    words = [w for w in normalized.split() if w not in stop_words]
    return ' '.join(words)

# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================

def token_required(f):
    """Decorator to require valid auth token for API routes"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        # Check for token in Authorization header
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            try:
                token = auth_header.split(' ')[1]
            except IndexError:
                return jsonify({'message': 'Invalid token format'}), 401
        
        if not token:
            return jsonify({'message': 'Token required'}), 401
        
        user_id = auth_db.verify_session(token)
        if not user_id:
            return jsonify({'message': 'Invalid or expired token'}), 401
        
        request.user_id = user_id
        return f(*args, **kwargs)
    
    return decorated

def login_required(f):
    """Decorator to require authentication for web page routes"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        # Check for token in cookies (set by frontend)
        token = request.cookies.get('auth_token')
        
        # Also check Authorization header as fallback
        if not token and 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            try:
                token = auth_header.split(' ')[1]
            except IndexError:
                pass
        
        if not token:
            return redirect(url_for('login_page'))
        
        user_id = auth_db.verify_session(token)
        if not user_id:
            # Clear invalid cookie and redirect to login
            response = redirect(url_for('login_page'))
            response.set_cookie('auth_token', '', expires=0)
            return response
        
        request.user_id = user_id
        return f(*args, **kwargs)
    
    return decorated

@app.route('/login', methods=['GET'])
def login_page():
    """Login page"""
    return render_template('login.html')

@app.route('/register', methods=['GET'])
def register_page():
    """Register page"""
    return render_template('register.html')

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    """Login API endpoint"""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'message': 'Username and password required'}), 400
    
    user_id = auth_db.authenticate_user(username, password)
    if not user_id:
        return jsonify({'message': 'Invalid username or password'}), 401
    
    token = auth_db.create_session(user_id)
    return jsonify({'token': token}), 200

@app.route('/api/auth/register', methods=['POST'])
def api_register():
    """Register API endpoint"""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'message': 'Username and password required'}), 400
    
    if len(username) < 3 or len(username) > 20:
        return jsonify({'message': 'Username must be 3-20 characters'}), 400
    
    if len(password) < 6:
        return jsonify({'message': 'Password must be at least 6 characters'}), 400
    
    if auth_db.create_user(username, password):
        return jsonify({'message': 'Account created successfully'}), 201
    else:
        return jsonify({'message': 'Username already exists'}), 409

@app.route('/api/auth/download-path', methods=['POST'])
@token_required
def set_download_path():
    """Set download path for current user"""
    data = request.get_json()
    download_path = data.get('download_path')
    
    if not download_path:
        return jsonify({'message': 'Download path required'}), 400
    
    # Store the original path to detect separator style before normalization
    original_path = download_path

    # Normalize the download path to use correct separators for current OS
    download_path = os.path.normpath(download_path)

    # Create nukedown folder in download path using consistent separator style
    # Detect separator style from original path - prefer forward slash if present, otherwise use OS default
    if '/' in original_path:
        # Use forward slash style (Unix/UNC paths)
        nukedown_folder = download_path.rstrip('\\/') + '/nukedown'
    else:
        # Use backslash style (Windows paths) or OS default
        nukedown_folder = os.path.join(download_path, 'nukedown')

    os.makedirs(nukedown_folder, exist_ok=True)
    
    # Store the base path, but return the nukedown subfolder path
    auth_db.set_download_path(request.user_id, download_path)
    return jsonify({'message': 'Download path saved successfully', 'download_path': nukedown_folder}), 200

@app.route('/api/auth/download-path', methods=['GET'])
@token_required
def get_download_path():
    """Get download path for current user"""
    path = auth_db.get_download_path(request.user_id)
    if path:
        # Normalize the path to use correct separators for current OS
        path = os.path.normpath(path)
        # Return the nukedown subfolder path
        path = os.path.join(path, 'nukedown')
    return jsonify({'download_path': path}), 200

@app.route('/api/auth/download-path', methods=['DELETE'])
@token_required
def delete_download_path():
    """Delete download path for current user"""
    auth_db.delete_download_path(request.user_id)
    return jsonify({'message': 'Download path deleted successfully'}), 200

@app.route('/api/auth/media-path', methods=['POST'])
@token_required
def save_media_path():
    """Save media path for current user"""
    data = request.get_json()
    path_name = data.get('path_name')
    media_path = data.get('media_path')
    
    if not path_name or not media_path:
        return jsonify({'message': 'Path name and media path required'}), 400
    
    os.makedirs(os.path.normpath(media_path), exist_ok=True)
    auth_db.save_media_path(request.user_id, path_name, media_path)
    return jsonify({'message': 'Media path saved successfully'}), 200

@app.route('/api/auth/media-paths', methods=['GET'])
@token_required
def get_media_paths():
    """Get all media paths for current user"""
    paths = auth_db.get_media_paths(request.user_id)
    
    # Filter out paths that don't exist and clean up database
    valid_paths = []
    for path_info in paths:
        media_path = path_info['media_path']
        if os.path.exists(media_path):
            valid_paths.append(path_info)
        else:
            # Remove non-existent path from database
            print(f"DEBUG: Removing non-existent media path from database: {media_path}")
            auth_db.delete_media_path(request.user_id, path_info['path_name'])
    
    return jsonify({'media_paths': valid_paths}), 200

@app.route('/api/auth/media-path', methods=['DELETE'])
@token_required
def delete_media_path():
    """Delete media path for current user"""
    data = request.get_json()
    path_name = data.get('path_name')
    
    if not path_name:
        return jsonify({'message': 'Path name required'}), 400
    
    if auth_db.delete_media_path(request.user_id, path_name):
        return jsonify({'message': 'Media path deleted successfully'}), 200
    else:
        return jsonify({'message': 'Cannot delete this path'}), 400

@app.route('/api/auth/change-password', methods=['POST'])
@token_required
def change_password():
    """Change user password"""
    data = request.get_json()
    old_password = data.get('old_password')
    new_password = data.get('new_password')
    
    if not old_password or not new_password:
        return jsonify({'message': 'Old password and new password required'}), 400
    
    if len(new_password) < 6:
        return jsonify({'message': 'New password must be at least 6 characters long'}), 400
    
    if auth_db.change_password(request.user_id, old_password, new_password):
        return jsonify({'message': 'Password changed successfully'}), 200
    else:
        return jsonify({'message': 'Current password is incorrect'}), 400

@app.route('/api/libraries/manga', methods=['GET'])
@token_required
def get_manga_library():
    """Get all manga from all media paths grouped by library"""
    try:
        # Get the auth token for cover URLs
        auth_token = request.headers.get('Authorization', '').replace('Bearer ', '')
        
        # First, scan and update the database with current file system state
        _scan_and_update_manga_library(request.user_id, auth_token)
        
        # Then return from database
        libraries = auth_db.get_manga_library(request.user_id)
        
        # Format response to match expected structure
        response_libraries = {}
        for library_name, manga_list in libraries.items():
            response_libraries[library_name] = {
                'path': '',  # We don't need this in the response anymore
                'manga': manga_list
            }
        
        return jsonify({'libraries': response_libraries}), 200
    except Exception as e:
        return jsonify({'message': f'Error loading libraries: {str(e)}'}), 500

def _scan_and_update_manga_library(user_id, auth_token):
    """Scan file system and update manga library database"""
    try:
        media_paths = auth_db.get_media_paths(user_id)
        
        # Track all found manga paths for cleanup
        found_manga_paths = set()
        
        for path_info in media_paths:
            media_path = os.path.normpath(path_info['media_path'])
            library_name = path_info['path_name']
            
            if not os.path.exists(media_path):
                continue
                
            # Walk through the media path and find manga directories
            for root, dirs, files in os.walk(media_path):
                # Skip subdirectories that are too deep (avoid going into chapter folders)
                depth = root.replace(media_path, '').count(os.sep)
                if depth > 1:  # Only look at top-level manga folders
                    continue
                    
                # Check if this directory contains manga files
                manga_files = [f for f in files if f.lower().endswith(('.cbz', '.cbr', '.pdf', '.epub'))]
                if manga_files:
                    # This is a manga directory
                    manga_title = os.path.basename(root)
                    manga_path = os.path.normpath(root)
                    found_manga_paths.add(manga_path)
                    
                    # Try to find a cover image named "cover"
                    cover_url = None
                    # Look for exact "cover.jpg" first, then other cover files (case-insensitive)
                    files_lower = [f.lower() for f in files]
                    cover_files = []
                    if 'cover.jpg' in files_lower:
                        cover_files = [files[files_lower.index('cover.jpg')]]
                    elif 'cover.png' in files_lower:
                        cover_files = [files[files_lower.index('cover.png')]]
                    elif 'cover.jpeg' in files_lower:
                        cover_files = [files[files_lower.index('cover.jpeg')]]
                    elif 'cover.webp' in files_lower:
                        cover_files = [files[files_lower.index('cover.webp')]]
                    elif 'cover.gif' in files_lower:
                        cover_files = [files[files_lower.index('cover.gif')]]
                    else:
                        # Fallback to any file starting with "cover."
                        cover_files = [f for f in files if f.lower().startswith('cover.') and f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif'))]
                    
                    if cover_files:
                        # Use the cover file
                        cover_filename = cover_files[0]
                        cover_path = os.path.join(root, cover_filename)
                        # Create a relative path that can be served - ensure forward slashes for URLs
                        # Normalize both paths to ensure consistent separators
                        media_path_normalized = os.path.normpath(media_path)
                        cover_path_normalized = os.path.normpath(cover_path)
                        relative_path = os.path.relpath(cover_path_normalized, media_path_normalized).replace('\\', '/')
                        cover_url = f'/api/libraries/cover/{urllib.parse.quote(relative_path)}?library={library_name}&token={auth_token}'
                        print(f"DEBUG: Found cover for {manga_title}: {cover_filename} -> {cover_url}")
                    else:
                        print(f"DEBUG: No cover found for {manga_title} in {root}. Files: {[f for f in files if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif'))]}")
                    
                    # Save/update in database
                    auth_db.save_manga_entry(user_id, library_name, manga_title, manga_path, cover_url, len(manga_files))
        
        # Clean up: remove database entries for manga that no longer exist
        all_db_entries = auth_db.get_all_manga_paths(user_id)
        for db_entry in all_db_entries:
            if db_entry not in found_manga_paths:
                print(f"DEBUG: Removing stale database entry: {db_entry}")
                auth_db.delete_manga_entry(user_id, db_entry)
        
    except Exception as e:
        print(f"Error scanning manga library: {str(e)}")

@app.route('/api/libraries/manga', methods=['DELETE'])
@token_required
def delete_manga():
    """Delete individual manga"""
    try:
        data = request.get_json()
        manga_path = data.get('manga_path')
        
        if not manga_path:
            return jsonify({'message': 'manga path required'}), 400
        
        # Security check: ensure the path is within user's media paths
        media_paths = auth_db.get_media_paths(request.user_id)
        is_allowed = False
        
        for path_info in media_paths:
            if manga_path.startswith(path_info['media_path']):
                is_allowed = True
                break
        
        if not is_allowed:
            return jsonify({'message': 'Access denied'}), 403
        
        # Delete from database first
        deleted_from_db = auth_db.delete_manga_entry(request.user_id, manga_path)
        print(f"DEBUG: Deleted from database: {deleted_from_db} for path: {manga_path}")
        
        # Then delete from file system
        if os.path.exists(manga_path) and os.path.isdir(manga_path):
            shutil.rmtree(manga_path)
            return jsonify({'message': 'manga deleted successfully', 'deleted_from_db': deleted_from_db}), 200
        else:
            return jsonify({'message': 'manga not found', 'deleted_from_db': deleted_from_db}), 404
    except Exception as e:
        return jsonify({'message': f'Error deleting manga: {str(e)}'}), 500

@app.route('/api/libraries/manga/all', methods=['DELETE'])
@token_required
def delete_all_manga():
    """Delete all manga from all libraries"""
    try:
        # Delete all entries from database first
        deleted_from_db = auth_db.delete_all_manga_entries(request.user_id)
        
        media_paths = auth_db.get_media_paths(request.user_id)
        deleted_count = 0
        
        for path_info in media_paths:
            media_path = path_info['media_path']
            if not os.path.exists(media_path):
                continue
            
            # Walk through and delete all manga directories
            for root, dirs, files in os.walk(media_path):
                # Only delete top-level manga directories
                depth = root.replace(media_path, '').count(os.sep)
                if depth == 1:  # Top-level manga folder
                    manga_files = [f for f in files if f.lower().endswith(('.cbz', '.cbr', '.pdf', '.epub'))]
                    if manga_files:
                        shutil.rmtree(root)
                        deleted_count += 1
        
        return jsonify({'message': f'All manga deleted successfully', 'deleted_count': deleted_count, 'deleted_from_db': deleted_from_db}), 200
    except Exception as e:
        return jsonify({'message': f'Error deleting all manga: {str(e)}'}), 500

@app.route('/api/libraries/manga/library/<library_name>', methods=['DELETE'])
@token_required
def delete_library_manga(library_name):
    """Delete all manga from a specific library"""
    try:
        # Get the media path for this library
        media_paths = auth_db.get_media_paths(request.user_id)
        library_path = None
        
        for path_info in media_paths:
            if path_info['path_name'] == library_name:
                library_path = path_info['media_path']
                break
        
        if not library_path:
            return jsonify({'message': f'Library "{library_name}" not found'}), 404
        
        # Delete entries from database for this library
        deleted_from_db = auth_db.delete_manga_entries_by_library(request.user_id, library_path)
        
        deleted_count = 0
        if os.path.exists(library_path):
            # Walk through and delete all manga directories in this library
            for root, dirs, files in os.walk(library_path):
                # Only delete top-level manga directories
                depth = root.replace(library_path, '').count(os.sep)
                if depth == 1:  # Top-level manga folder
                    manga_files = [f for f in files if f.lower().endswith(('.cbz', '.cbr', '.pdf', '.epub'))]
                    if manga_files:
                        shutil.rmtree(root)
                        deleted_count += 1
        
        return jsonify({'message': f'All manga in library "{library_name}" deleted successfully', 'deleted_count': deleted_count, 'deleted_from_db': deleted_from_db}), 200
    except Exception as e:
        return jsonify({'message': f'Error deleting manga from library "{library_name}": {str(e)}'}), 500

@app.route('/api/libraries/all', methods=['DELETE'])
@token_required
def delete_all_libraries():
    """Delete all media paths"""
    try:
        media_paths = auth_db.get_media_paths(request.user_id)
        deleted_count = 0
        
        for path_info in media_paths:
            auth_db.delete_media_path(request.user_id, path_info['path_name'])
            deleted_count += 1
        
        return jsonify({'message': 'All libraries deleted successfully', 'deleted_count': deleted_count}), 200
    except Exception as e:
        return jsonify({'message': f'Error deleting all libraries: {str(e)}'}), 500


@app.route('/api/auth/logout', methods=['POST'])
@token_required
def api_logout():
    """Logout API endpoint"""
    token = None
    
    # Get token from Authorization header
    if 'Authorization' in request.headers:
        auth_header = request.headers['Authorization']
        try:
            token = auth_header.split(' ')[1]
        except IndexError:
            return jsonify({'message': 'Invalid token format'}), 401
    
    if token:
        auth_db.invalidate_session(token)
    
    return jsonify({'message': 'Logged out successfully'}), 200

@app.route('/api/auth/paths', methods=['GET'])
@token_required
def get_user_paths():
    """Get all paths for current user (deprecated - use separate endpoints)"""
    download_path = auth_db.get_download_path(request.user_id)
    media_paths = auth_db.get_media_paths(request.user_id)
    return jsonify({'download_path': download_path, 'media_paths': media_paths}), 200

@app.route('/api/auth/browse', methods=['POST'])
@token_required
def browse_directories():
    """Browse directories on the system"""
    data = request.get_json()
    # Use cross-platform default path
    default_path = os.path.expanduser('~') if os.name != 'nt' else 'C:\\'
    path = data.get('path', default_path)
    
    try:
        dirs = []
        for item in os.listdir(path):
            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                dirs.append(item_path)
        dirs.sort()
        return jsonify({'directories': dirs[:20]}), 200  # Limit to 20 results
    except Exception as e:
        return jsonify({'message': f'Error browsing: {str(e)}'}), 400

@app.route('/favicon.ico')
def favicon():
    """Serve favicon"""
    from flask import send_from_directory
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.svg', mimetype='image/svg+xml')

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'UP'})

@app.route('/')
@login_required
def index():
    """Home page - template will check auth with localStorage"""
    query = request.args.get('q', '')
    return render_template('search.html', query=query)

@app.route('/search')
@login_required
def search_page():
    """Redirect search route to home"""
    query = request.args.get('q', '')
    return render_template('search.html', query=query)


# In-memory downloads database (in production, use real database)
downloads_db = []  # Keep for backward compatibility, but will be replaced with database

@app.route('/api/downloads/<int:download_id>', methods=['DELETE'])
@token_required
def delete_download(download_id):
    """Delete a download entry from history and remove files if they exist"""
    user_id = request.user_id
    
    try:
        # First, get the download details to know what files to remove
        downloads = auth_db.get_user_downloads(user_id)
        download = next((d for d in downloads if d['id'] == download_id), None)
        
        if not download:
            return jsonify({'error': 'Download not found'}), 404
        
        # If download is in progress, mark it as cancelled so background thread stops
        if download.get('status') in ['pending', 'downloading']:
            auth_db.update_download(download_id, user_id, {
                'status': 'cancelled',
                'error': 'Download cancelled by user'
            })
            print(f"✗ Download cancelled: {download.get('title', 'Unknown')}")
            # Give thread a moment to see the cancellation
            import time
            time.sleep(0.1)
        
        # Remove files from filesystem if they exist
        files_removed = []
        errors = []
        
        # NOTE: Never delete the destination folder - preserve downloaded manga files
        # Only clean up temp files and partial downloads
        destination = download.get('destination', '')
        if destination and os.path.exists(destination):
            print(f"✓ Preserved destination folder: {destination}")
        
        # Check temp path for cleanup of partial downloads
        temp_path = download.get('temp_path', '')
        if temp_path and temp_path != destination and os.path.exists(temp_path):
            # Don't delete the main nukedown download folder
            if os.path.basename(temp_path) == 'nukedown':
                print(f"✓ Preserved nukedown download folder: {temp_path}")
                # For cancelled downloads, look for manga-specific subdirectories to clean up
                if download.get('status') == 'cancelled':
                    manga_title = download.get('title', '')
                    if manga_title and os.path.isdir(temp_path):
                        # Look for directories that might contain the manga
                        for item in os.listdir(temp_path):
                            item_path = os.path.join(temp_path, item)
                            if os.path.isdir(item_path) and (manga_title.lower() in item.lower() or item.lower() in manga_title.lower()):
                                try:
                                    shutil.rmtree(item_path)
                                    files_removed.append(f"Cancelled manga directory: {item_path}")
                                    print(f"🗑️ Removed cancelled manga directory: {item_path}")
                                except Exception as e:
                                    error_msg = f"Failed to remove cancelled manga directory {item_path}: {e}"
                                    errors.append(error_msg)
                                    print(f"⚠️ {error_msg}")
            else:
                try:
                    if os.path.isdir(temp_path):
                        shutil.rmtree(temp_path)
                        files_removed.append(f"Temp directory: {temp_path}")
                        print(f"🗑️ Removed temp directory: {temp_path}")
                    else:
                        os.remove(temp_path)
                        files_removed.append(f"Temp file: {temp_path}")
                        print(f"🗑️ Removed temp file: {temp_path}")
                except Exception as e:
                    error_msg = f"Failed to remove temp path {temp_path}: {e}"
                    errors.append(error_msg)
                    print(f"⚠️ {error_msg}")
        
        # Remove from database
        if auth_db.delete_download(download_id, user_id):
            message = 'Download deleted'
            if files_removed:
                message += f' and {len(files_removed)} file(s) removed from disk'
            if errors:
                message += f' (with {len(errors)} error(s))'
            
            return jsonify({
                'message': message,
                'files_removed': files_removed,
                'errors': errors
            }), 200
        else:
            return jsonify({'error': 'Failed to delete download from database'}), 500
            
    except Exception as e:
        return jsonify({'error': 'Failed to delete download'}), 500

@app.route('/api/browse-directories', methods=['POST'])
def api_browse_directories():
    """Browse directories from a given path"""
    data = request.get_json()
    current_path = data.get('path', '').strip()
    
    if not current_path:
        # Return common starting paths
        if os.name == 'nt':  # Windows
            drives = []
            import string
            for drive in string.ascii_uppercase:
                drive_path = f"{drive}:\\"
                if os.path.exists(drive_path):
                    try:
                        # Check if drive is accessible
                        os.listdir(drive_path)
                        drives.append({
                            'name': drive_path,
                            'path': drive_path,
                            'is_dir': True,
                            'display': f"{drive}: Drive"
                        })
                    except (PermissionError, OSError):
                        pass
            return jsonify({'directories': drives, 'current': None, 'breadcrumb': []})
        else:  # Linux/Mac
            # Return root filesystem and common user directories
            paths = []
            root_paths = ['/', '/home', '/usr', '/var', '/opt']
            for root_path in root_paths:
                if os.path.exists(root_path):
                    try:
                        os.listdir(root_path)
                        display_name = root_path if root_path != '/' else 'Root Filesystem'
                        paths.append({
                            'name': root_path,
                            'path': root_path,
                            'is_dir': True,
                            'display': display_name
                        })
                    except (PermissionError, OSError):
                        pass
            # Add user home directory
            home_path = os.path.expanduser('~')
            if home_path not in [p['path'] for p in paths]:
                paths.append({
                    'name': home_path,
                    'path': home_path,
                    'is_dir': True,
                    'display': 'Home Directory'
                })
            return jsonify({'directories': paths, 'current': None, 'breadcrumb': []})
    
    # Normalize and expand path
    current_path = os.path.expanduser(current_path)
    current_path = os.path.normpath(current_path)
    
    # Validate path exists
    if not os.path.exists(current_path):
        return jsonify({'error': f'Path does not exist: {current_path}'}), 400
    
    if not os.path.isdir(current_path):
        return jsonify({'error': f'Path is not a directory: {current_path}'}), 400
    
    try:
        # Build breadcrumb
        breadcrumb = []
        parts = current_path.split(os.sep)
        # Filter out empty parts (from leading /)
        parts = [p for p in parts if p]
        
        for i, part in enumerate(parts):
            if os.name == 'nt' and i == 0:
                # Windows drive like "C:"
                path = part + os.sep
            else:
                path = os.sep + os.sep.join(parts[:i+1]) if i == 0 and not os.name == 'nt' else os.sep.join(parts[:i+1])
            
            if path:
                breadcrumb.append({
                    'name': part,
                    'path': path
                })
        
        # Get parent directory
        parent = os.path.dirname(current_path)
        parent_item = None
        if parent and parent != current_path:
            parent_item = {
                'name': '.. (up one level)',
                'path': parent,
                'is_dir': True,
                'is_parent': True
            }
        
        # Get subdirectories
        directories = []
        try:
            entries = os.listdir(current_path)
            for entry in sorted(entries, key=str.lower):
                full_path = os.path.join(current_path, entry)
                try:
                    if os.path.isdir(full_path):
                        # Skip hidden directories on Unix
                        if os.name != 'nt' and entry.startswith('.'):
                            continue
                        directories.append({
                            'name': entry,
                            'path': full_path,
                            'is_dir': True
                        })
                except (PermissionError, OSError):
                    # Skip directories we can't access
                    continue
        except (PermissionError, OSError) as e:
            return jsonify({'error': f'Cannot read directory: {str(e)}'}), 400
        
        result = {'current': current_path, 'directories': [], 'breadcrumb': breadcrumb}
        if parent_item:
            result['directories'].append(parent_item)
        result['directories'].extend(directories)
        
        return jsonify(result)
    
    except Exception as e:
        return jsonify({'error': f'Error browsing directory: {str(e)}'}), 500


@app.route('/api/downloads/config', methods=['GET', 'PUT'])
@token_required
def downloads_config():
    """Get or set downloads configuration"""
    # Default to /downloads/manga for Docker setup
    # Can be overridden via API
    if not hasattr(downloads_config, 'destination'):
        downloads_config.destination = '/downloads/manga'
    
    if request.method == 'GET':
        return jsonify({
            'destination': downloads_config.destination
        })
    else:
        data = request.get_json()
        if data.get('destination'):
            downloads_config.destination = data['destination']
            return jsonify({
                'destination': downloads_config.destination,
                'message': 'Configuration saved'
            })
        return jsonify({'error': 'Invalid configuration'}), 400


@app.route('/api/youtube_download', methods=['POST'])
@token_required
def youtube_download():
    """Download a YouTube video"""
    data = request.get_json()
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL required'}), 400
    
    # Get user's download path
    download_path = auth_db.get_download_path(request.user_id)
    if not download_path:
        return jsonify({'error': 'Download path not configured'}), 400
    
    # Create YouTube folder if it doesn't exist
    youtube_dir = os.path.join(download_path, 'YouTube')
    os.makedirs(youtube_dir, exist_ok=True)
    
    # Start download in background thread
    def download_video():
        try:
            ydl_opts = {
                'outtmpl': os.path.join(youtube_dir, '%(title)s.%(ext)s'),
                'format': 'best[height<=1080]',  # Limit to 1080p
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as e:
            print(f"YouTube download error: {e}")
    
    thread = threading.Thread(target=download_video)
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'message': 'Download started'}), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5100))
    debug = os.environ.get('FLASK_ENV') != 'production'
    print(f"Starting nukedown ({'Production' if not debug else 'Development'}) on http://localhost:{port}")
    print("Access the web interface to request manga downloads")
    app.run(host='0.0.0.0', port=port, debug=debug)
