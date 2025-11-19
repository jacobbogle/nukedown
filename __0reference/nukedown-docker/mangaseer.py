#!/usr/bin/env python3
"""
nukedown - Simple manga Request Interface
A Jellyseerr-like interface for requesting manga downloads
Now using direct manga site integration for better results
"""

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

# Import our new manga connectors and index system
from manga_connectors import mangaConnectorManager
from manga_index import mangaIndexDB, search_indexed_manga
# Use the enhanced downloader with HakuNeko integration
try:
    from manga_downloader_enhanced import get_downloader, get_queue
except ImportError:
    from manga_downloader import get_downloader, get_queue

# Import authentication module
from auth import AuthDB

app = Flask(__name__, template_folder='templates')
auth_db = AuthDB('config/nukedown.db')

# ============================================================================
# SEARCH CACHE - Reduces redundant database queries and API calls
# ============================================================================
class SearchCache:
    """Simple in-memory cache for search results with TTL"""
    def __init__(self, ttl_seconds=3600):
        self.cache = {}
        self.ttl_seconds = ttl_seconds
    
    def get_key(self, query):
        """Generate cache key from query"""
        normalized = query.lower().strip()
        return hashlib.md5(normalized.encode()).hexdigest()
    
    def get(self, query):
        """Get cached results if not expired"""
        key = self.get_key(query)
        if key in self.cache:
            result, timestamp = self.cache[key]
            if datetime.now() - timestamp < timedelta(seconds=self.ttl_seconds):
                print(f"[CACHE HIT] Query: {query}")
                return result
            else:
                del self.cache[key]  # Expired, remove it
        return None
    
    def set(self, query, result):
        """Cache search results"""
        key = self.get_key(query)
        self.cache[key] = (result, datetime.now())
        print(f"[CACHE SET] Query: {query} ({len(result.get('results', []))} results)")
    
    def clear(self):
        """Clear cache"""
        self.cache.clear()

# Initialize search cache with 1-hour TTL
search_cache = SearchCache(ttl_seconds=3600)

# Initialize manga connector manager
manga_manager = mangaConnectorManager()

# Available manga sources (direct reading sites only)
manga_SOURCES = [
    {"id": "omegascans", "name": "OmegaScans", "type": "direct", "priority": 1, "description": "High-quality webtoons and manga"},
    {"id": "mangafox", "name": "mangaFox", "type": "direct", "priority": 1, "description": "Large collection of English manga"}
]

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

def calculate_relevance_score(title, query):
    """Calculate relevance score for search results"""
    import re
    title_lower = title.lower()
    query_lower = query.lower()

    score = 0

    # Exact match gets highest score
    if title_lower == query_lower:
        score += 100

    # Starts with query
    if title_lower.startswith(query_lower):
        score += 50

    # Contains whole query words
    query_words = set(query_lower.split())
    title_words = set(title_lower.split())
    matching_words = query_words.intersection(title_words)
    score += len(matching_words) * 20

    # Contains query as substring
    if query_lower in title_lower:
        score += 10

    # Fuzzy matching - each character of query appears in order
    query_chars = list(query_lower.replace(' ', ''))
    title_chars = list(title_lower.replace(' ', ''))

    fuzzy_score = 0
    query_idx = 0
    for char in title_chars:
        if query_idx < len(query_chars) and char == query_chars[query_idx]:
            fuzzy_score += 1
            query_idx += 1

    if fuzzy_score > 0:
        score += fuzzy_score * 2

    return score

def deduplicate_results(results, query):
    """Remove duplicates and keep best matches"""
    seen_titles = {}
    deduplicated = []

    for result in results:
        normalized = normalize_title(result['title'])

        # Calculate relevance score
        relevance = calculate_relevance_score(result['title'], query)

        if normalized not in seen_titles:
            # First time seeing this title
            result['relevance_score'] = relevance
            seen_titles[normalized] = result
            deduplicated.append(result)
        else:
            # Duplicate - keep the one with higher relevance or better source priority
            existing = seen_titles[normalized]
            existing_relevance = existing.get('relevance_score', 0)

            if relevance > existing_relevance:
                # Replace with better match
                result['relevance_score'] = relevance
                seen_titles[normalized] = result
                # Find and replace in deduplicated list
                for i, r in enumerate(deduplicated):
                    if normalize_title(r['title']) == normalized:
                        deduplicated[i] = result
                        break
            elif relevance == existing_relevance:
                # Same relevance, prefer higher priority source
                if result.get('indexer_priority', 99) < existing.get('indexer_priority', 99):
                    result['relevance_score'] = relevance
                    seen_titles[normalized] = result
                    for i, r in enumerate(deduplicated):
                        if normalize_title(r['title']) == normalized:
                            deduplicated[i] = result
                            break

    return deduplicated

    # Return CBZ/EPUB results first, then others
    return cbz_epub_results + other_results

def get_manga_cover_url(title):
    """Generate a fast local SVG placeholder cover URL based on title hash"""
    # Fast local SVG placeholder - no network call needed
    import hashlib
    title_hash = hashlib.md5(title.encode()).hexdigest()[:8]
    
    # Generate colors based on hash for visual variety
    hash_int = int(title_hash, 16)
    colors = [
        '#FF6B35', '#F7931E', '#FDB913', '#009B77',
        '#00A651', '#0066CC', '#5033C7', '#B5006B'
    ]
    color = colors[hash_int % len(colors)]
    
    # Return local SVG endpoint - instant rendering, no network call
    return f"/placeholder/{title_hash}/{color.replace('#', '')}"

@app.route('/placeholder/<hash>/<color>')
def placeholder_cover(hash, color):
    """Generate fast inline SVG placeholder covers"""
    # Validate color is hex
    if not all(c in '0123456789ABCDEFabcdef' for c in color) or len(color) != 6:
        color = 'FF6B35'
    
    # Generate SVG inline - cached by browser
    svg = f'''<svg width="200" height="300" xmlns="http://www.w3.org/2000/svg">
        <defs>
            <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" style="stop-color:#{color};stop-opacity:0.9" />
                <stop offset="100%" style="stop-color:#{color}88;stop-opacity:0.7" />
            </linearGradient>
            <filter id="blur">
                <feGaussianBlur in="SourceGraphic" stdDeviation="1" />
            </filter>
        </defs>
        <rect width="200" height="300" fill="url(#grad)" />
        <text x="100" y="150" font-family="Arial" font-size="16" fill="#ffffff" text-anchor="middle" dominant-baseline="middle" font-weight="bold">
            📖 manga
        </text>
        <text x="100" y="170" font-family="Arial" font-size="12" fill="#ffffff80" text-anchor="middle" dominant-baseline="middle">
            Loading...
        </text>
    </svg>'''
    
    from flask import Response
    response = Response(svg, mimetype='image/svg+xml')
    response.headers['Cache-Control'] = 'public, max-age=86400'  # Cache for 1 day
    return response

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

@app.route('/api/libraries/cover/<path:cover_path>')
def get_manga_cover(cover_path):
    """Serve manga cover images"""
    try:
        # Get auth token from query parameter or header
        auth_token = request.args.get('token') or request.headers.get('Authorization', '').replace('Bearer ', '')
        if not auth_token:
            return jsonify({'message': 'Authentication required'}), 401
        
        # Verify token and get user_id
        user_id = auth_db.verify_session(auth_token)
        if not user_id:
            return jsonify({'message': 'Invalid or expired token'}), 401
        
        library_name = request.args.get('library')
        if not library_name:
            return jsonify({'message': 'Library parameter required'}), 400
        
        # Find the media path for this library
        media_paths = auth_db.get_media_paths(user_id)
        media_path = None
        
        for path_info in media_paths:
            if path_info['path_name'] == library_name:
                media_path = os.path.normpath(path_info['media_path'])
                break
        
        if not media_path:
            return jsonify({'message': 'Library not found'}), 404
        
        # Construct the full path - handle both forward and backward slashes
        # The cover_path comes from URL so it has forward slashes, but we need to handle Windows paths
        cover_path_normalized = cover_path.replace('/', os.sep)
        media_path_normalized = os.path.normpath(media_path)
        full_path = os.path.join(media_path_normalized, cover_path_normalized)
        
        print(f"DEBUG: Serving cover - library: {library_name}, cover_path: {cover_path}, normalized: {cover_path_normalized}, full_path: {full_path}, exists: {os.path.exists(full_path)}")
        
        # Security check: ensure the path is within the media path
        if not os.path.abspath(full_path).startswith(os.path.abspath(media_path)):
            print(f"DEBUG: Security check failed for {full_path}")
            return jsonify({'message': 'Access denied'}), 403
        
        if os.path.exists(full_path):
            from flask import send_file
            # Determine MIME type based on file extension
            _, ext = os.path.splitext(full_path.lower())
            mime_types = {
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.webp': 'image/webp',
                '.gif': 'image/gif'
            }
            mime_type = mime_types.get(ext, 'image/jpeg')
            print(f"DEBUG: Sending file {full_path} with MIME type {mime_type}")
            return send_file(full_path, mimetype=mime_type)
        else:
            # Return a placeholder
            placeholder_path = os.path.join(app.root_path, 'static', 'placeholder-cover.svg')
            if os.path.exists(placeholder_path):
                return send_file(placeholder_path, mimetype='image/svg+xml')
            else:
                return jsonify({'message': 'Cover not found'}), 404
    except Exception as e:
        return jsonify({'message': f'Error serving cover: {str(e)}'}), 500

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

@app.route('/api/search')
@token_required
def api_search():
    query = request.args.get('q', '').strip()
    sources_param = request.args.get('sources', '').strip()
    filter_adult = request.args.get('filter_adult', 'true').lower() == 'true'

    if not query:
        return jsonify({'error': 'Query parameter required'}), 400

    # Parse selected sources
    if sources_param:
        selected_sources = [s.strip() for s in sources_param.split(',') if s.strip()]
    else:
        # Default to all sources if not specified
        selected_sources = ['mangafox', 'omegascans', 'hentaifox']

    # Apply adult content filter - remove adult sources if filter is enabled
    if filter_adult:
        selected_sources = [s for s in selected_sources if s not in ['omegascans', 'hentaifox']]

    # Create cache key including sources and filter
    cache_key = f"{query}|{'|'.join(sorted(selected_sources))}|adult_filter_{filter_adult}"
    
    # Check cache first
    cached_result = search_cache.get(cache_key)
    if cached_result:
        return jsonify(cached_result)

    try:
        all_results = []
        
        # For HentaiFox, use direct API search if selected
        if 'hentaifox' in selected_sources:
            try:
                hentaifox_connector = manga_manager.get_connector('hentaifox')
                if hentaifox_connector:
                    # Search HentaiFox API directly
                    hf_results = hentaifox_connector.search(query, page=1, sort='latest')
                    
                    for manga in hf_results[:20]:  # Limit to 20 results
                        formatted_result = {
                            'title': manga['title'],
                            'url': manga['link'],
                            'source': 'hentaifox',
                            'type': 'manga',
                            'language': 'english',
                            'cover_url': manga.get('cover') or get_manga_cover_url(manga['title']),
                            'actual_cover_url': manga.get('cover'),
                            'manga_id': manga.get('id'),
                            'description': f"Category: {manga.get('category', 'Unknown')}",
                            'seeders': 999,
                            'leechers': 0,
                            'size_mb': 0,
                            'category': manga.get('category', 'manga'),
                            'publish_date': '',
                            'chapters': 1,  # HentaiFox galleries are single chapter
                            'status': 'completed',
                            'direct_source': True,
                            'indexer': 'hentaifox',
                            'indexer_priority': 3
                        }
                        all_results.append(formatted_result)
            except Exception as e:
                logger.error(f"Error searching HentaiFox API: {e}")
        
        # For mangaFox and OmegaScans, use direct connector search
        direct_sources = [s for s in selected_sources if s in ['mangafox', 'omegascans']]
        for source in direct_sources:
            try:
                connector = manga_manager.get_connector(source)
                if connector:
                    # Search directly using connector
                    search_results = connector.search_manga(query, limit=30)  # Increased limit per source

                    for manga in search_results:
                        # Filter adult content from mangaFox if filter is enabled
                        if filter_adult and source == 'mangafox':
                            # Check genre tags for adult content first (more reliable)
                            genres = manga.get('genres', [])
                            adult_genres = ['adult', 'erotica', 'smut', 'pornographic', 'hentai', 'mature', 'yaoi', 'yuri', 'boys\' love', 'girls\' love']
                            
                            # If any adult genre is present, skip this result
                            if any(genre in adult_genres for genre in genres):
                                continue
                            
                            # Fallback: Check title keywords if genres not available or incomplete
                            title_lower = manga['title'].lower()
                            adult_keywords = ['hentai', 'adult', 'mature', 'ecchi', 'yaoi', 'yuri', 'porn', 'sex', 'nsfw', 'erotica', 'xxx']
                            if any(keyword in title_lower for keyword in adult_keywords):
                                continue  # Skip this result

                        # Skip expensive cover fetching during search - use lazy loading
                        # Cover will be fetched when needed via /api/cover/detail endpoint
                        formatted_result = {
                            'title': manga['title'],
                            'url': manga['url'],
                            'source': source,
                            'type': 'manga',
                            'language': 'english',
                            'cover_url': None,  # Will be loaded lazily
                            'actual_cover_url': None,
                            'manga_id': manga.get('id'),
                            'description': f"Source: {source.title()}",
                            'seeders': 999,
                            'leechers': 0,
                            'size_mb': 0,
                            'category': 'manga',
                            'publish_date': '',
                            'chapters': 0,
                            'status': 'unknown',
                            'direct_source': True,
                            'indexer': source,
                            'indexer_priority': 1 if source == 'omegascans' else 2
                        }
                        all_results.append(formatted_result)
            except Exception as e:
                logger.error(f"Error searching {source}: {e}")
        
        # For other sources, use the index
        indexed_sources = [s for s in selected_sources if s not in ['hentaifox', 'mangafox', 'omegascans']]
        if indexed_sources:
            indexed_results = search_indexed_manga(query, limit=20)
            
            # Filter results by selected indexed sources
            indexed_results = [r for r in indexed_results if r.get('source') in indexed_sources]
            
            for manga in indexed_results:
                # Always use placeholder initially, let frontend fetch actual covers
                cover_url = get_manga_cover_url(manga['title'])
                actual_cover_url = None
                
                # Skip expensive detail fetching - just use cached data from index
                formatted_result = {
                    'title': manga['title'],
                    'url': manga['url'],
                    'source': manga['source'],
                    'type': 'manga',
                    'language': manga['language'],
                    'cover_url': cover_url,
                    'actual_cover_url': actual_cover_url,
                    'manga_id': manga.get('id') or manga.get('manga_id'),
                    'description': '',
                    'seeders': 999,
                    'leechers': 0,
                    'size_mb': 0,
                    'category': 'manga',
                    'publish_date': manga['last_updated'],
                    'chapters': 0,
                    'status': manga.get('status', 'unknown'),
                    'direct_source': True,  # Enable cover fetching for indexed results
                    'indexer': manga['source'],
                    'indexer_priority': 1 if manga['source'] == 'omegascans' else 2
                }
                all_results.append(formatted_result)
        
        if not all_results:
            # No results found
            response = {
                'query': query,
                'total_results': 0,
                'results': [],
                'message': 'No results found. Try different keywords or enable more sources.'
            }
            search_cache.set(cache_key, response)
            return jsonify(response)

        # Remove duplicates and sort by relevance
        deduplicated_results = deduplicate_results(all_results, query)

        # Sort by relevance score (descending), then by source priority, then by title
        deduplicated_results.sort(key=lambda x: (
            -x.get('relevance_score', 0),  # Higher relevance first
            x.get('indexer_priority', 99),  # Lower priority number first
            x['title'].lower()
        ))

        # Limit to top 50 results for performance
        final_results = deduplicated_results[:50]

        response = {
            'query': query,
            'total_results': len(final_results),
            'results': final_results,
            'search_type': 'mixed' if 'hentaifox' in selected_sources and indexed_sources else ('api' if 'hentaifox' in selected_sources else 'indexed'),
            'sources': selected_sources,
            'deduplicated_from': len(all_results)
        }
        search_cache.set(cache_key, response)
        return jsonify(response)

    except Exception as e:
        print(f"Error in search: {e}")
        return jsonify({'error': 'Search failed'}), 500


@app.route('/api/cover/detail/<source>/<manga_id>')
@token_required
def api_cover_detail(source, manga_id):
    """Fetch actual cover image for a manga (check database first, then async fetch)"""
    try:
        # First check if we have a cover URL stored in the database
        db = mangaIndexDB('config/manga_index.db')
        conn = sqlite3.connect(db.db_path)
        try:
            cursor = conn.cursor()
            try:
                cursor.execute('SELECT cover_url FROM manga_index WHERE source = ? AND manga_id = ?', 
                              (source, manga_id))
                result = cursor.fetchone()
                if result and result[0]:
                    stored_cover_url = result[0]
                    print(f"✅ Found stored cover URL for {source}/{manga_id}: {stored_cover_url[:80]}")
                    import base64
                    encoded = base64.urlsafe_b64encode(stored_cover_url.encode()).decode()
                    proxied = f"/cover/{source}/{encoded}"
                    return jsonify({'cover_url': proxied, 'original_cover_url': stored_cover_url, 'source': source, 'cached': True})
            except sqlite3.OperationalError as e:
                if "no such column" in str(e):
                    print(f"⚠️ cover_url column not found in database, skipping cache check")
                else:
                    raise
        finally:
            conn.close()
        
        # If not in database, proceed with async fetching
        print(f"📡 Cover not cached for {source}/{manga_id}, fetching asynchronously...")
        
        # Get the connector for this source
        connector = manga_manager.get_connector(source)
        if not connector:
            return jsonify({'cover_url': None, 'error': 'Source not found'}), 404
        
        # We need to get the URL from the database to fetch details
        # Try to find the manga in the index
        db = mangaIndexDB('config/manga_index.db')
        conn = sqlite3.connect(db.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT url, title FROM manga_index WHERE source = ? AND manga_id = ?', 
                          (source, manga_id))
            result = cursor.fetchone()
        finally:
            conn.close()
        
        if not result:
            # If not found in local index, let's try to construct a fallback URL
            # based on the source and manga_id. This helps when no index is present.
            print(f"⚠️ No DB entry for {source}/{manga_id}; attempting fallback lookup")
            manga_url = None
            title = None
            try:
                if source.lower() in ['mangafox', 'fanfox']:
                    manga_url = f"https://mangahub.us/manga/{manga_id}"
                elif source.lower() in ['omegascans', 'omegascans.org']:
                    # If manga_id is a JSON blob with a slug, parse it
                    try:
                        import json as _json
                        data = _json.loads(manga_id) if isinstance(manga_id, str) else manga_id
                        slug = data.get('slug') if isinstance(data, dict) else None
                        if slug:
                            manga_url = f"https://omegascans.org/series/{slug}"
                    except Exception:
                        manga_url = None
                elif source.lower() in ['hentaifox', 'hentaifox.com']:
                    manga_url = f"https://hentaifox.com/gallery/{manga_id}/"
            except Exception as e:
                print(f"Error building fallback URL for {source}/{manga_id}: {e}")
        else:
            manga_url, title = result
        
        # Try to get cover details with timeout
        import threading
        import time
        
        cover_result = {'url': None}
        error_result = {'error': None}
        
        def fetch_cover_async():
            try:
                manga_obj = {
                    'source': source,
                    'manga_id': manga_id,
                    'id': manga_id,
                    'url': manga_url,
                    'title': title
                }
                # If no url/title and source is OmegaScans and manga_id is a slug, build minimal id JSON
                try:
                    import json as _json
                    if source.lower() in ['omegascans', 'omegascans.org'] and manga_obj.get('id') and isinstance(manga_obj.get('id'), str):
                        try:
                            # If string isn't JSON and looks like a slug, create JSON with slug
                            _ = _json.loads(manga_obj['id'])
                        except Exception:
                            # Create JSON string with slug to assist connector parsing
                            manga_obj['id'] = _json.dumps({'slug': manga_obj['id']})
                except Exception:
                    pass
                
                # Call connector to get details
                print('DEBUG: Fetching cover with manga_obj:', manga_obj)
                if hasattr(connector, 'get_manga_details'):
                    details = connector.get_manga_details(manga_obj)
                    if details and details.get('cover_url'):
                        cover_result['url'] = details['cover_url']
                        print(f"✅ Got cover from {source} for {title}: {details['cover_url'][:80]}")
                    else:
                        print(f"⚠️ No cover URL in details from {source} for {title}")
                        error_result['error'] = 'No cover_url in details'
                else:
                    print(f"⚠️ Connector doesn't have get_manga_details method")
                    error_result['error'] = 'Method not available'
                    
            except Exception as e:
                print(f"❌ Error fetching cover from {source} for {title}: {str(e)[:100]}")
                error_result['error'] = str(e)
        
        # Start fetch in background thread with timeout
        thread = threading.Thread(target=fetch_cover_async, daemon=True)
        thread.start()
        thread.join(timeout=8)  # Wait up to 8 seconds
        
        if thread.is_alive():
            print(f"⏱️ Cover fetch timeout for {source}/{manga_id}")
            return jsonify({
                'cover_url': None,
                'error': 'Fetch timeout',
                'source': source,
                'manga_id': manga_id
            })
            return jsonify({
                'cover_url': None,
                'error': error_msg,
                'source': source,
                'manga_id': manga_id
            }), 500
        
        # At this point, the fetch completed (or not) — return the URL if found
        if cover_result.get('url'):
            import base64
            encoded = base64.urlsafe_b64encode(cover_result['url'].encode()).decode()
            proxied = f"/cover/{source}/{encoded}"
            return jsonify({'cover_url': proxied, 'original_cover_url': cover_result['url'], 'source': source})
        else:
            return jsonify({'cover_url': None, 'error': error_result.get('error', 'No cover found'), 'source': source}), 404
            
    except Exception as e:
        print(f"Error in cover detail endpoint: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# In-memory downloads database (in production, use real database)
downloads_db = []  # Keep for backward compatibility, but will be replaced with database

@app.route('/api/downloads', methods=['GET'])
@token_required
def get_downloads():
    """Get list of all downloads with status"""
    user_id = request.user_id
    
    try:
        downloads = auth_db.get_user_downloads(user_id)
        stats = auth_db.get_download_stats(user_id)
        
        return jsonify({
            'downloads': downloads,
            'total': stats.get('total', 0),
            'pending': stats.get('pending', 0),
            'completed': stats.get('completed', 0),
            'failed': stats.get('failed', 0)
        })
    except Exception as e:
        logger.error(f"Error getting downloads: {e}")
        return jsonify({'error': 'Failed to load downloads'}), 500

@app.route('/api/downloads', methods=['POST'])
@token_required
def add_download():
    """Add a new download"""
    user_id = request.user_id
    
    data = request.get_json()
    
    print("\n" + "="*80)
    print("DEBUG: RECEIVED DOWNLOAD REQUEST")
    print("="*80)
    print(f"Raw JSON data: {data}")
    print(f"destination type: {type(data.get('destination'))}")
    print(f"destination repr: {repr(data.get('destination'))}")
    print(f"destination display: {data.get('destination')}")
    print("="*80 + "\n")
    
    if not data.get('title') or not data.get('destination'):
        return jsonify({'error': 'title and destination required'}), 400
    
    download_data = {
        'title': data['title'],
        'source': data.get('source', 'unknown'),
        'url': data.get('url', ''),
        'manga_id': data.get('manga_id'),  # Store manga_id for chapter fetching
        'cover_url': data.get('cover_url'),  # Cover image URL
        'temp_path': data.get('temp_path'),  # Temporary download location
        'destination': data['destination'],  # Final media path destination
        'status': 'pending',  # pending, completed, failed
        'progress': 0,
        'error': None
    }
    
    try:
        download_id = auth_db.add_download(user_id, download_data)
        download_data['id'] = download_id
        download_data['added_at'] = datetime.now().isoformat()
        
        print(f"Created download with cover_url: {download_data.get('cover_url')}")
        return jsonify(download_data), 201
    except Exception as e:
        logger.error(f"Error adding download: {e}")
        return jsonify({'error': 'Failed to add download'}), 500

@app.route('/api/downloads/<int:download_id>', methods=['PUT'])
@token_required
def update_download(download_id):
    """Update download status"""
    user_id = request.user_id
    
    data = request.get_json()
    
    update_data = {}
    if 'status' in data:
        update_data['status'] = data['status']
    if 'progress' in data:
        update_data['progress'] = data['progress']
    if 'error' in data:
        update_data['error'] = data['error']
    if data.get('status') == 'completed':
        update_data['completed_at'] = datetime.now().isoformat()
    
    try:
        if auth_db.update_download(download_id, user_id, update_data):
            return jsonify({'message': 'Download updated'})
        else:
            return jsonify({'error': 'Download not found'}), 404
    except Exception as e:
        logger.error(f"Error updating download: {e}")
        return jsonify({'error': 'Failed to update download'}), 500

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
        logger.error(f"Error deleting download: {e}")
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

@app.route('/api/downloads/<int:download_id>/start', methods=['POST'])
@token_required
def start_download(download_id):
    """Start a manga download in the background"""
    user_id = request.user_id
    
    data = request.get_json() or {}
    
    # Find the download in database
    downloads = auth_db.get_user_downloads(user_id)
    download_item = next((d for d in downloads if d['id'] == download_id), None)
    
    if not download_item:
        return jsonify({'error': 'Download not found'}), 404
    
    # Update status to downloading
    auth_db.update_download(download_id, user_id, {'status': 'downloading', 'progress': 0})
    
    # Create a background thread to handle the download
    def background_download():
        try:
            # Check if already cancelled before starting
            current_downloads = auth_db.get_user_downloads(user_id)
            current_download = next((d for d in current_downloads if d['id'] == download_id), None)
            if not current_download or current_download.get('status') == 'cancelled':
                return
                
            downloader = get_downloader(connector_manager=manga_manager)
            
            def update_progress(percent, message):
                # Check if download was cancelled
                current_downloads = auth_db.get_user_downloads(user_id)
                current_download = next((d for d in current_downloads if d['id'] == download_id), None)
                if not current_download or current_download.get('status') == 'cancelled':
                    raise Exception("Download cancelled by user")
                
                auth_db.update_download(download_id, user_id, {'progress': int(percent)})
                print(f"[{current_download['title']}] {message} ({percent}%)")
            
            # Check cancellation again before starting download
            current_downloads = auth_db.get_user_downloads(user_id)
            current_download = next((d for d in current_downloads if d['id'] == download_id), None)
            if not current_download or current_download.get('status') == 'cancelled':
                return
            
            # Always download to temp path first
            temp_path = current_download.get('temp_path')
            if not temp_path:
                # Fallback if temp_path not set
                temp_path = current_download['destination']
            
            print("\n" + "="*80)
            print("DEBUG: STARTING BACKGROUND DOWNLOAD")
            print("="*80)
            print(f"download_item['destination'] repr: {repr(current_download['destination'])}")
            print(f"download_item['destination'] display: {current_download['destination']}")
            print(f"temp_path: {temp_path}")
            print("="*80 + "\n")
            
            manga_info = {
                'title': current_download['title'],
                'manga_id': current_download.get('manga_id'),  # Get from stored download_item
                'source': current_download.get('source'),  # Get from stored download_item
                'url': current_download.get('url'),  # Get from stored download_item
                'cover_url': current_download.get('cover_url'),  # Cover image URL
                'destination': temp_path  # Download to temp location
            }
            
            print(f"Starting download with cover_url: {manga_info.get('cover_url')}")
            
            # Final cancellation check before starting download
            current_downloads = auth_db.get_user_downloads(user_id)
            current_download = next((d for d in current_downloads if d['id'] == download_id), None)
            if not current_download or current_download.get('status') == 'cancelled':
                return
            
            result = downloader.download_manga(manga_info, progress_callback=update_progress)
            
            # Check if cancelled or removed before processing result
            current_downloads = auth_db.get_user_downloads(user_id)
            current_download = next((d for d in current_downloads if d['id'] == download_id), None)
            if not current_download or current_download.get('status') == 'cancelled':
                return
            
            if result.get('success'):
                # Check again if still exists
                current_downloads = auth_db.get_user_downloads(user_id)
                current_download = next((d for d in current_downloads if d['id'] == download_id), None)
                if not current_download:
                    return
                    
                # Download succeeded - now move to final destination if different from temp
                final_destination = current_download['destination']
                if temp_path != final_destination and result.get('file_path'):
                    try:
                        update_progress(95, "Organizing to final location...")
                        
                        # Get the source series directory
                        source_series_dir = result.get('file_path')
                        
                        # Create destination series dir (same structure)
                        series_name = os.path.basename(source_series_dir)
                        
                        print("\n" + "="*80)
                        print("DEBUG: PATH OPERATIONS")
                        print("="*80)
                        print(f"source_series_dir: {source_series_dir}")
                        print(f"series_name: {series_name}")
                        print(f"final_destination BEFORE normpath: {repr(final_destination)}")
                        print(f"final_destination BEFORE normpath display: {final_destination}")
                        
                        # Normalize paths to preserve backslashes
                        final_destination = os.path.normpath(final_destination)
                        final_series_path = os.path.normpath(os.path.join(final_destination, series_name))
                        
                        print(f"final_destination AFTER normpath: {repr(final_destination)}")
                        print(f"final_destination AFTER normpath display: {final_destination}")
                        print(f"final_series_path: {repr(final_series_path)}")
                        print(f"final_series_path display: {final_series_path}")
                        print("="*80 + "\n")
                        
                        # Create destination directory if it doesn't exist
                        os.makedirs(final_destination, exist_ok=True)
                        
                        # Move the series directory
                        if os.path.exists(final_series_path):
                            # If destination already exists, merge content
                            for item in os.listdir(source_series_dir):
                                src = os.path.join(source_series_dir, item)
                                dst = os.path.join(final_series_path, item)
                                if os.path.isfile(src):
                                    shutil.copy2(src, dst)
                                elif os.path.isdir(src) and not os.path.exists(dst):
                                    shutil.copytree(src, dst)
                            # Remove temp directory
                            shutil.rmtree(source_series_dir)
                            print(f"✓ Merged {source_series_dir} to {final_series_path}")
                        else:
                            # Clean move
                            shutil.move(source_series_dir, final_series_path)
                            print(f"✓ Moved {source_series_dir} to {final_series_path}")
                        
                        # Clean up empty manga title directories from downloads path
                        try:
                            # Only clean up if the source was within the nukedown downloads folder
                            if temp_path and os.path.basename(temp_path) == 'nukedown':
                                # Clean up the specific manga directory that was moved
                                if os.path.exists(source_series_dir) and not os.listdir(source_series_dir):
                                    os.rmdir(source_series_dir)
                                    print(f"✓ Cleaned up empty manga directory: {source_series_dir}")
                                
                                # Clean up any empty parent directories within nukedown (but not nukedown itself)
                                parent_dir = os.path.dirname(source_series_dir)
                                while parent_dir and parent_dir != temp_path and os.path.exists(parent_dir):
                                    if not os.listdir(parent_dir):
                                        os.rmdir(parent_dir)
                                        print(f"✓ Cleaned up empty parent directory: {parent_dir}")
                                        parent_dir = os.path.dirname(parent_dir)
                                    else:
                                        break
                                
                                print(f"✓ Preserved nukedown download folder: {temp_path}")
                            else:
                                print(f"✓ Skipped cleanup for non-nukedown path: {temp_path}")
                        except Exception as cleanup_error:
                            print(f"Cleanup warning: {cleanup_error}")
                    
                    except Exception as move_error:
                        print(f"Move error: {move_error}")
                        auth_db.update_download(download_id, user_id, {
                            'status': 'failed',
                            'error': f"Downloaded but failed to organize: {move_error}"
                        })
                        return
                
                auth_db.update_download(download_id, user_id, {
                    'status': 'completed',
                    'progress': 100,
                    'destination': final_series_path  # Update to actual final path
                })
                print(f"✓ Download completed and organized: {current_download['title']}")
            else:
                auth_db.update_download(download_id, user_id, {
                    'status': 'failed',
                    'error': result.get('error', 'Unknown error')
                })
                print(f"✗ Download failed: {current_download['title']} - {result.get('error', 'Unknown error')}")
        
        except Exception as e:
            # Don't update status if it was already cancelled
            current_downloads = auth_db.get_user_downloads(user_id)
            current_download = next((d for d in current_downloads if d['id'] == download_id), None)
            if current_download and current_download.get('status') != 'cancelled':
                auth_db.update_download(download_id, user_id, {
                    'status': 'failed',
                    'error': str(e)
                })
                print(f"✗ Download error: {str(e)}")
    
    # Start background thread
    download_thread = threading.Thread(target=background_download, daemon=True)
    download_thread.start()
    
    return jsonify({
        'id': download_id,
        'status': 'downloading',
        'message': 'Download started'
    })

# @app.route('/api/index/stats')
# def api_index_stats():
#     """Get manga index statistics"""
#     try:
#         stats = get_manga_stats()
#         return jsonify(stats)
#     except Exception as e:
#         return jsonify({'error': str(e)}), 500


# @app.route('/api/index/update', methods=['POST'])
# def api_update_index():
#     """Update the manga index"""
#     try:
#         def progress_callback(message):
#             # In a real implementation, you'd use WebSocket or SSE for real-time updates
#             print(f"[INDEX UPDATE] {message}")
#
#         total_count = update_manga_index(progress_callback)
#
#         return jsonify({
#             'success': True,
#             'total_indexed': total_count,
#             'message': f'Index updated successfully. Indexed {total_count} manga.'
#         })
#
#     except Exception as e:
#         return jsonify({
#             'success': False,
#             'error': str(e),
#             'message': 'Index update failed'
#         }), 500


@app.route('/cover/<source>/<path:encoded_url>')
def cover_proxy_by_source(source, encoded_url):
    """Proxy cover images using source-aware headers and decoding."""
    import base64, urllib.parse
    try:
        decoded = base64.urlsafe_b64decode(encoded_url.encode()).decode()
    except Exception as e:
        print(f"Error decoding cover URL: {e}")
        return Response(b"Invalid URL", status=400, content_type='text/plain')
    # Delegate to the main cover proxy with source hints
    return _proxy_cover_with_source(source, decoded)


def _proxy_cover_with_source(source, url):
    """Internal helper to fetch remote image with conditional headers by source."""
    session = requests.Session()
    # Default headers - match the connector's headers
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    # Conditional headers per source
    if source and source.lower() in ['mangafox', 'fanfox', 'fanfox.net', 'mangahub']:
        session.cookies.set('isAdult', '1')
        headers.update({
            'Referer': 'https://mangahub.us/', 
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'x-cookie': 'isAdult=1'
        })
    elif source and source.lower() in ['omegascans', 'omegascans.org', 'omegascans']:
        headers.update({'Referer': 'https://omegascans.org/', 'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8'})
    else:
        headers.update({'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8'})
    try:
        response = session.get(url, timeout=10, stream=True, headers=headers)
        response.raise_for_status()
        return Response(response.content, content_type=response.headers.get('content-type', 'image/jpeg'), headers={'Cache-Control': 'public, max-age=86400', 'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        print(f"Error proxying cover image {url} for source {source}: {e}")
        # Return placeholder
        placeholder_url = get_manga_cover_url("placeholder")
        try:
            placeholder_response = requests.get(placeholder_url, timeout=5)
            return Response(placeholder_response.content, content_type='image/jpeg', headers={'Cache-Control': 'public, max-age=3600'})
        except:
            return Response(b"Image not available", status=404, content_type='text/plain')

@app.route('/cover/<path:url>')
def cover_proxy(url):
    """Legacy proxy handler that decodes an already-encoded URL (without source)."""
    # Try to detect whether the url is base64 encoded (from our proxied link) or a direct URL
    import base64
    try:
        decoded = base64.urlsafe_b64decode(url.encode()).decode()
        # Can't determine source; use default proxy behavior
        return _proxy_cover_with_source(None, decoded)
    except Exception:
        # Not base64; treat as raw URL
        return _proxy_cover_with_source(None, url)
    """Proxy cover images that require special headers/cookies"""
    try:
        # Decode the URL
        import urllib.parse
        decoded_url = urllib.parse.unquote(url)
        
        # Create a session with proper headers for mangaFox
        session = requests.Session()
        session.cookies.set('isAdult', '1')
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': 'https://fanfox.net/',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })
        
        # Fetch the image
        response = session.get(decoded_url, timeout=10, stream=True)
        response.raise_for_status()
        
        # Return the image with proper headers
        return Response(
            response.content,
            content_type=response.headers.get('content-type', 'image/jpeg'),
            headers={
                'Cache-Control': 'public, max-age=86400',  # Cache for 24 hours
                'Access-Control-Allow-Origin': '*'
            }
        )
        
    except Exception as e:
        print(f"Error proxying cover image {url}: {e}")
        # Return a placeholder image
        placeholder_url = get_manga_cover_url("placeholder")
        try:
            placeholder_response = requests.get(placeholder_url, timeout=5)
            return Response(
                placeholder_response.content,
                content_type='image/jpeg',
                headers={'Cache-Control': 'public, max-age=3600'}
            )
        except:
            # Return a simple error response
            return Response(b"Image not available", status=404, content_type='text/plain')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5100))
    debug = os.environ.get('FLASK_ENV') != 'production'
    print(f"Starting nukedown ({'Production' if not debug else 'Development'}) on http://localhost:{port}")
    print("Access the web interface to request manga downloads")
    app.run(host='0.0.0.0', port=port, debug=debug)
