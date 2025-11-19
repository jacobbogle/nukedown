#!/usr/bin/env python3
"""
manga Index Management System
Creates and manages a local SQLite database of manga titles for fast searching.
Similar to HakuNeko's index approach - stores minimal data for search, full data on demand.
"""

import sqlite3
import os
import json
import time
import logging
from datetime import datetime, timedelta
from manga_connectors import mangaConnectorManager

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class mangaIndexDB:
    """Manages the local manga index database"""
    
    def __init__(self, db_path='manga_index.db'):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the SQLite database with required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create manga index table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS manga_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            normalized_title TEXT NOT NULL,
            manga_id TEXT,
            status TEXT,
            language TEXT DEFAULT 'english',
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, url)
        )
        ''')
        
        # Add cover_url column if it doesn't exist (for backward compatibility)
        try:
            cursor.execute('ALTER TABLE manga_index ADD COLUMN cover_url TEXT')
        except sqlite3.OperationalError:
            # Column already exists
            pass
        
        # Create index update log table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS index_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            total_count INTEGER DEFAULT 0,
            success BOOLEAN DEFAULT FALSE,
            error_message TEXT
        )
        ''')
        
        # Create indexes for fast searching
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_normalized_title ON manga_index(normalized_title)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_source ON manga_index(source)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_title ON manga_index(title)')
        
        conn.commit()
        conn.close()
    
    def normalize_title(self, title):
        """Normalize title for better searching"""
        import re
        # Convert to lowercase, remove special characters, collapse whitespace
        normalized = re.sub(r'[^\w\s]', '', title.lower())
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized
    
    def add_manga(self, source, title, url, manga_id=None, status=None, language='english', cover_url=None):
        """Add a manga to the index"""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            normalized_title = self.normalize_title(title)
            
            cursor.execute('''
            INSERT OR REPLACE INTO manga_index 
            (source, title, url, normalized_title, manga_id, status, language, cover_url, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (source, title, url, normalized_title, manga_id, status, language, cover_url))
            
            conn.commit()
        finally:
            conn.close()
    
    def search_manga(self, query, limit=50):
        """Search manga index by title"""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            normalized_query = self.normalize_title(query)
            
            # Search with LIKE for partial matches
            cursor.execute('''
            SELECT source, title, url, manga_id, status, language, last_updated, cover_url
            FROM manga_index 
            WHERE normalized_title LIKE ? OR title LIKE ?
            ORDER BY 
                CASE 
                    WHEN normalized_title = ? THEN 1
                    WHEN normalized_title LIKE ? THEN 2
                    WHEN title LIKE ? THEN 3
                    ELSE 4
                END,
                title
            LIMIT ?
            ''', (
                f'%{normalized_query}%',
                f'%{query}%',
                normalized_query,
                f'{normalized_query}%',
                f'{query}%',
                limit
            ))
            
            results = cursor.fetchall()
            
            return [
                {
                    'source': row[0],
                    'title': row[1],
                    'url': row[2],
                    'manga_id': row[3],
                    'status': row[4],
                    'language': row[5],
                    'last_updated': row[6],
                    'cover_url': row[7]
                }
                for row in results
            ]
        finally:
            conn.close()
    
    def get_index_stats(self):
        """Get statistics about the index"""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            cursor.execute('SELECT source, COUNT(*) FROM manga_index GROUP BY source')
            source_counts = dict(cursor.fetchall())
            
            cursor.execute('SELECT COUNT(*) FROM manga_index')
            total_count = cursor.fetchone()[0]
            
            cursor.execute('SELECT MAX(last_updated) FROM manga_index')
            last_updated = cursor.fetchone()[0]
            
            return {
                'total_manga': total_count,
                'by_source': source_counts,
                'last_updated': last_updated
            }
        finally:
            conn.close()
    
    def clear_source_index(self, source):
        """Clear index for a specific source"""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM manga_index WHERE source = ?', (source,))
            deleted_count = cursor.rowcount
            conn.commit()
            return deleted_count
        finally:
            conn.close()
    
    def log_update_start(self, source):
        """Log the start of an index update"""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute('''
            INSERT INTO index_updates (source, started_at)
            VALUES (?, CURRENT_TIMESTAMP)
            ''', (source,))
            update_id = cursor.lastrowid
            conn.commit()
            return update_id
        finally:
            conn.close()
    
    def log_update_complete(self, update_id, total_count, success=True, error_message=None):
        """Log the completion of an index update"""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute('''
            UPDATE index_updates 
            SET completed_at = CURRENT_TIMESTAMP, total_count = ?, success = ?, error_message = ?
            WHERE id = ?
            ''', (total_count, success, error_message, update_id))
            conn.commit()
        finally:
            conn.close()


class mangaIndexBuilder:
    """Builds and updates the manga index from various sources"""
    
    def __init__(self):
        self.db = mangaIndexDB('config/manga_index.db')
        self.connector_manager = mangaConnectorManager()
    
    def build_mangafox_index(self, callback=None, test_mode=False):
        """Build index from mangaFox"""
        logger.info("Building mangaFox index...")
        update_id = self.db.log_update_start('mangafox')
        
        try:
            connector = self.connector_manager.get_connector('mangafox')
            
            # For testing, just get first few pages
            if test_mode:
                manga_list = connector.get_mangas(limit=50)
            else:
                manga_list = connector.get_mangas()
            
            count = 0
            for manga in manga_list:
                # Fetch cover URL for this manga
                cover_url = None
                try:
                    details = connector.get_manga_details(manga)
                    cover_url = details.get('cover_url')
                    if cover_url:
                        logger.debug(f"Found cover for {manga['title']}: {cover_url[:50]}...")
                except Exception as e:
                    logger.warning(f"Could not fetch cover for {manga['title']}: {e}")
                
                self.db.add_manga(
                    source='mangafox',
                    title=manga['title'],
                    url=manga['url'],
                    manga_id=manga.get('id'),
                    status=manga.get('status'),
                    language='english',
                    cover_url=cover_url
                )
                count += 1
                
                if callback and count % 10 == 0:  # Update more frequently for cover fetching
                    callback(f"Indexed {count} manga from mangaHub (with covers)")
            
            self.db.log_update_complete(update_id, count, True)
            logger.info(f"mangaHub index complete: {count} manga")
            return count
            
        except Exception as e:
            logger.error(f"Error building mangaFox index: {e}")
            self.db.log_update_complete(update_id, 0, False, str(e))
            raise
    
    def build_omegascans_index(self, callback=None, test_mode=False):
        """Build index from OmegaScans"""
        logger.info("Building OmegaScans index...")
        update_id = self.db.log_update_start('omegascans')
        
        try:
            connector = self.connector_manager.get_connector('omegascans')
            
            # For testing, just get first few pages
            if test_mode:
                manga_list = connector.get_mangas(limit=200)
            else:
                manga_list = connector.get_mangas()
            
            count = 0
            for manga in manga_list:
                # Store the full manga ID (JSON string for Omega Scans)
                manga_id = manga.get('id')
                
                self.db.add_manga(
                    source='omegascans',
                    title=manga['title'],
                    url=manga['url'],
                    manga_id=manga_id,
                    status=manga.get('status'),
                    language='english'
                )
                count += 1
                
                if callback and count % 50 == 0:
                    callback(f"Indexed {count} manga from OmegaScans")
            
            self.db.log_update_complete(update_id, count, True)
            logger.info(f"OmegaScans index complete: {count} manga")
            return count
            
        except Exception as e:
            logger.error(f"Error building OmegaScans index: {e}")
            self.db.log_update_complete(update_id, 0, False, str(e))
            raise
    
    def build_hentaifox_index(self, callback=None, test_mode=False):
        """Build index from HentaiFox popular galleries using common tags"""
        logger.info("Building HentaiFox index...")
        update_id = self.db.log_update_start('hentaifox')
        
        try:
            connector = self.connector_manager.get_connector('hentaifox')
            
            # HentaiFox requires search keywords, so we'll use popular tags
            # to build a diverse index
            popular_tags = [
                'romance', 'fantasy', 'schoolgirl', 'anal', 'blowjob',
                'vanilla', 'creampie', 'ahegao', 'netorare', 'incest',
                'milf', 'loli', 'teacher', 'maid', 'pregnant'
            ] if not test_mode else ['romance', 'fantasy', 'vanilla']
            
            max_pages_per_tag = 2 if test_mode else 5
            count = 0
            seen_ids = set()  # Track to avoid duplicates
            
            for tag in popular_tags:
                try:
                    for page in range(1, max_pages_per_tag + 1):
                        try:
                            results = connector.search(tag, page=page, sort="popular")
                            
                            if not results:
                                break  # No more results for this tag
                            
                            for manga in results:
                                manga_id = manga.get('id')
                                if manga_id and manga_id not in seen_ids:
                                    self.db.add_manga(
                                        source='hentaifox',
                                        title=manga['title'],
                                        url=manga['link'],
                                        manga_id=manga_id,
                                        status='completed',
                                        language='english'
                                    )
                                    seen_ids.add(manga_id)
                                    count += 1
                            
                            # Rate limiting
                            import time
                            time.sleep(1)
                            
                        except Exception as e:
                            logger.error(f"Error indexing HentaiFox tag '{tag}' page {page}: {e}")
                            continue
                    
                    if callback:
                        callback(f"Indexed {count} galleries from HentaiFox (tag: {tag})")
                    
                except Exception as e:
                    logger.error(f"Error indexing HentaiFox tag '{tag}': {e}")
                    continue
            
            self.db.log_update_complete(update_id, count, True)
            logger.info(f"HentaiFox index complete: {count} galleries")
            return count
            
        except Exception as e:
            logger.error(f"Error building HentaiFox index: {e}")
            self.db.log_update_complete(update_id, 0, False, str(e))
            raise
    
    def build_full_index(self, callback=None, test_mode=False):
        """Build complete index from all sources"""
        logger.info("Building complete manga index...")
        
        total_count = 0
        
        if callback:
            callback("Starting mangaFox indexing...")
        
        try:
            count = self.build_mangafox_index(callback, test_mode)
            total_count += count
        except Exception as e:
            logger.error(f"mangaFox indexing failed: {e}")
            if callback:
                callback(f"mangaFox indexing failed: {e}")
        
        if callback:
            callback("Starting OmegaScans indexing...")
        
        try:
            count = self.build_omegascans_index(callback, test_mode)
            total_count += count
        except Exception as e:
            logger.error(f"OmegaScans indexing failed: {e}")
            if callback:
                callback(f"OmegaScans indexing failed: {e}")
        
        # HentaiFox is provided via API - do not build a local index for this source
        # (We intentionally skip HentaiFox indexing. If local indexing is needed in
        # the future, re-add the call to self.build_hentaifox_index.)
        if callback:
            callback("Skipping HentaiFox indexing (using remote API)")
        
        if callback:
            callback(f"Index complete! Total: {total_count} manga")
        
        return total_count
    
    def should_update_index(self, max_age_hours=24):
        """Check if index should be updated based on age"""
        stats = self.db.get_index_stats()
        
        if stats['total_manga'] == 0:
            return True, "Index is empty"
        
        if not stats['last_updated']:
            return True, "No update timestamp"
        
        try:
            last_updated = datetime.fromisoformat(stats['last_updated'])
            age = datetime.now() - last_updated
            
            if age > timedelta(hours=max_age_hours):
                return True, f"Index is {age.days} days old"
        except:
            return True, "Cannot parse update time"
        
        return False, "Index is current"


def search_indexed_manga(query, limit=50):
    """Convenience function to search the manga index"""
    db = mangaIndexDB('config/manga_index.db')
    return db.search_manga(query, limit)


def get_manga_stats():
    """Convenience function to get index statistics"""
    db = mangaIndexDB('config/manga_index.db')
    return db.get_index_stats()


def update_manga_index(callback=None):
    """Convenience function to update the manga index"""
    builder = mangaIndexBuilder()
    return builder.build_full_index(callback)


if __name__ == '__main__':
    # Command line interface for testing
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == 'build':
            print("Building manga index...")
            def progress_callback(message):
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
            
            count = update_manga_index(progress_callback)
            print(f"Index built successfully: {count} manga")
            
        elif command == 'search':
            if len(sys.argv) > 2:
                query = ' '.join(sys.argv[2:])
                results = search_indexed_manga(query, 10)
                print(f"Found {len(results)} results for '{query}':")
                for result in results:
                    print(f"  [{result['source']}] {result['title']}")
            else:
                print("Usage: python manga_index.py search <query>")
                
        elif command == 'stats':
            stats = get_manga_stats()
            print("manga Index Statistics:")
            print(f"  Total manga: {stats['total_manga']}")
            print(f"  Last updated: {stats['last_updated']}")
            print("  By source:")
            for source, count in stats['by_source'].items():
                print(f"    {source}: {count}")
        
        else:
            print("Commands: build, search <query>, stats")
    else:
        print("manga Index Manager")
        print("Commands: build, search <query>, stats")
