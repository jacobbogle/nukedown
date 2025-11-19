"""
manga Downloader Module
Handles downloading manga from direct sources and converting to formats suitable for Jellyfin
"""

import os
import json
import requests
import zipfile
import io
from pathlib import Path
from datetime import datetime
import logging
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class mangaDownloader:
    """Downloads manga from sources and converts to CBZ format for Jellyfin"""
    
    def __init__(self, base_destination="/downloads/manga"):
        """
        Initialize downloader
        base_destination: Should point to /downloads/manga in Docker setup
                        Will organize as: /downloads/manga/[Series Name]/[Chapters as CBZ]
        """
        self.base_destination = base_destination
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'x-cookie': 'isAdult=1'
        })
    
    def download_manga(self, manga_info: dict, progress_callback=None) -> dict:
        """
        Download a manga and convert to CBZ format for Jellyfin
        
        Jellyfin structure created:
        /downloads/manga/[Series Name]/
            - series.json (metadata)
            - chapter_001.cbz
            - chapter_002.cbz
            etc.
        
        Args:
            manga_info: {
                'title': str,
                'manga_id': str,
                'source': str (mangafox, omegascans),
                'url': str,
                'destination': str (optional, defaults to /downloads/manga)
            }
            progress_callback: function(progress_percent, message)
        
        Returns:
            {
                'success': bool,
                'message': str,
                'file_path': str (if successful, path to series folder),
                'error': str (if failed)
            }
        """
        try:
            # Use provided destination or default
            destination = manga_info.get('destination', self.base_destination)
            if not destination:
                destination = self.base_destination
            
            title = manga_info.get('title', 'Unknown')
            source = manga_info.get('source', 'unknown')
            url = manga_info.get('url', '')
            
            if progress_callback:
                progress_callback(10, "Initializing download...")
            
            # Create series directory for Jellyfin
            # Format: /downloads/manga/Series Name/
            series_dir = os.path.join(destination, self._sanitize_filename(title))
            try:
                os.makedirs(series_dir, exist_ok=True)
            except PermissionError as e:
                error_msg = (
                    f"Permission denied creating directory: {series_dir}\n"
                    f"This usually means:\n"
                    f"1. /downloads volume is read-only\n"
                    f"2. Container user doesn't have write permissions\n"
                    f"3. Destination path doesn't exist\n"
                    f"Check your Docker volume configuration."
                )
                logger.error(error_msg)
                return {
                    'success': False,
                    'error': error_msg
                }
            except OSError as e:
                if e.errno == 30:  # Read-only filesystem
                    error_msg = (
                        f"Read-only filesystem: {destination}\n"
                        f"Your /downloads volume is mounted as read-only.\n"
                        f"In docker-compose.yml, ensure:\n"
                        f"  volumes:\n"
                        f"    - ./downloads/manga:/downloads/manga\n"
                        f"NOT with :ro flag"
                    )
                    logger.error(error_msg)
                    return {
                        'success': False,
                        'error': error_msg
                    }
                raise
            
            if progress_callback:
                progress_callback(20, "Fetching manga chapters...")
            
            # Get chapters from source
            if source.lower() == 'mangafox':
                chapters = self._fetch_mangafox_chapters(url)
            elif source.lower() == 'omegascans':
                chapters = self._fetch_omegascans_chapters(url)
            else:
                chapters = []
            
            if not chapters:
                return {
                    'success': False,
                    'error': f'No chapters found on {source}'
                }
            
            if progress_callback:
                progress_callback(30, f"Found {len(chapters)} chapters")
            
            # Download chapters
            downloaded_chapters = 0
            for i, chapter in enumerate(chapters):
                if progress_callback:
                    percent = 30 + (i / len(chapters)) * 50
                    progress_callback(percent, f"Downloading chapter {i + 1}/{len(chapters)}...")
                
                images = self._download_chapter(chapter['url'], chapter.get('number', str(i + 1)))
                if images:
                    # Create separate CBZ for each chapter
                    cbz_filename = f"chapter_{int(float(chapter['number'])):03d}.cbz"
                    cbz_path = os.path.join(series_dir, cbz_filename)
                    self._create_cbz(cbz_path, images)
                    downloaded_chapters += 1
            
            if downloaded_chapters == 0:
                return {
                    'success': False,
                    'error': 'Failed to download any chapters'
                }
            
            if progress_callback:
                progress_callback(95, "Organizing for Jellyfin...")
            
            # Create metadata for Jellyfin
            self._create_jellyfin_metadata(series_dir, title, source)
            
            if progress_callback:
                progress_callback(100, "Complete!")
            
            return {
                'success': True,
                'message': f'Downloaded {downloaded_chapters} chapters',
                'file_path': series_dir
            }
        
        except Exception as e:
            logger.error(f"Download error: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def _fetch_mangafox_chapters(self, url: str) -> list:
        """
        Fetch chapter list from mangaFox
        Returns list of chapters with URLs
        Example: [
            {'number': '1', 'title': 'Chapter 1', 'url': 'https://...'},
            {'number': '2', 'title': 'Chapter 2', 'url': 'https://...'},
        ]
        """
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            base_url = '/'.join(url.split('/')[:3])  # e.g., https://mangahub.us
            
            chapters = []
            for a in soup.find_all('a', href=True):
                if '/chapter/' in a['href']:
                    title = "Chapter " + a.text.strip().lstrip('#')
                    if a['href'].startswith('http'):
                        link = a['href']
                    else:
                        link = base_url + a['href']
                    # Extract chapter number from title or href
                    chapter_num = title.replace('Chapter ', '').strip()
                    chapters.append({
                        'number': chapter_num,
                        'title': title,
                        'url': link
                    })
            
            # Remove duplicates and sort
            seen_urls = set()
            unique_chapters = []
            for chapter in chapters:
                if chapter['url'] not in seen_urls:
                    seen_urls.add(chapter['url'])
                    unique_chapters.append(chapter)
            
            # Sort by chapter number
            def chapter_key(chapter):
                try:
                    return float(chapter['number'])
                except ValueError:
                    return 0
            
            unique_chapters.sort(key=chapter_key)
            
            logger.info(f"Found {len(unique_chapters)} chapters on mangaFox")
            return unique_chapters
            
        except Exception as e:
            logger.error(f"Error fetching mangaFox chapters: {e}")
            return []
    
    def _fetch_omegascans_chapters(self, url: str) -> list:
        """
        Fetch chapter list from OmegaScans
        Similar structure to mangaFox but with different URL patterns
        """
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            import re
            
            chapters = []
            # OmegaScans typically has chapters in a different structure
            # Look for chapter links
            
            chapter_pattern = r'/chapters?/(\d+(?:\.\d+)?)'
            chapter_urls = re.findall(chapter_pattern, response.text)
            
            if chapter_urls:
                for i, chapter_num in enumerate(set(chapter_urls)):
                    chapter_url = url.rstrip('/') + f'/chapters/{chapter_num}/'
                    chapters.append({
                        'number': chapter_num,
                        'title': f'Chapter {chapter_num}',
                        'url': chapter_url
                    })
            else:
                chapters = [{'number': '1', 'title': 'Chapter 1', 'url': url}]
            
            logger.info(f"Found {len(chapters)} chapters on OmegaScans")
            return sorted(chapters, key=lambda x: float(x['number']))
            
        except Exception as e:
            logger.error(f"Error fetching OmegaScans chapters: {e}")
            return []
    
    def _download_chapter(self, url: str, chapter_num: str) -> list:
        """
        Download images from a chapter page
        Extracts all image URLs and downloads them as byte data
        Returns list of (filename, image_data) tuples
        """
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            images = []
            
            # Extract image URLs from img tags, specifically from imgx.mghcdn.com
            for img in soup.find_all('img', src=True):
                if 'imgx.mghcdn.com' in img['src']:
                    img_url = img['src']
                    
                    try:
                        img_response = self.session.get(img_url, timeout=5)
                        if img_response.status_code == 200:
                            # Determine file extension
                            ext = url.split('.')[-1].lower()
                            if ext not in ['jpg', 'jpeg', 'png', 'gif']:
                                ext = 'jpg'
                            
                            filename = f"{len(images)+1:04d}.{ext}"
                            images.append((filename, img_response.content))
                            
                            logger.debug(f"Downloaded image {len(images)} for chapter {chapter_num}")
                    except Exception as img_err:
                        logger.warning(f"Failed to download image {len(images)+1}: {img_err}")
            
            logger.info(f"Successfully downloaded {len(images)} images from chapter {chapter_num}")
            return images
        
        except Exception as e:
            logger.error(f"Error downloading chapter {chapter_num}: {e}")
            return []
    
    def _create_cbz(self, cbz_path: str, images: list) -> bool:
        """
        Create a CBZ file (ZIP archive) from downloaded images
        Images should be list of (filename, image_data) tuples
        Returns True if successful, False otherwise
        """
        if not images:
            logger.warning(f"No images to create CBZ: {cbz_path}")
            return False
        
        try:
            import zipfile
            
            with zipfile.ZipFile(cbz_path, 'w', zipfile.ZIP_DEFLATED) as cbz:
                for filename, image_data in images:
                    # Write image to archive
                    cbz.writestr(filename, image_data)
                    logger.debug(f"Added {filename} to CBZ")
            
            file_size_mb = os.path.getsize(cbz_path) / (1024 * 1024)
            logger.info(f"Created CBZ: {cbz_path} ({file_size_mb:.2f} MB)")
            return True
        
        except PermissionError as e:
            logger.error(f"Permission denied creating CBZ {cbz_path}: {e}")
            logger.error("Check /downloads volume write permissions in Docker")
            return False
        except OSError as e:
            if e.errno == 30:  # Read-only filesystem
                logger.error(f"Read-only filesystem: Cannot create {cbz_path}")
                logger.error("Mount /downloads volume with write permissions (remove :ro flag)")
                return False
            logger.error(f"OS Error creating CBZ {cbz_path}: {e}")
            return False
        except Exception as e:
            logger.error(f"Error creating CBZ {cbz_path}: {e}")
            if os.path.exists(cbz_path):
                try:
                    os.remove(cbz_path)
                except:
                    pass
            return False
    
    def _create_jellyfin_metadata(self, manga_dir: str, title: str, source: str):
        """Create metadata files for Jellyfin recognition"""
        try:
            # Create series.json for Jellyfin
            metadata = {
                'Name': title,
                'Source': source,
                'Type': 'manga',
                'DateAdded': datetime.now().isoformat(),
                'Provider': 'nukedown'
            }
            
            metadata_path = os.path.join(manga_dir, 'series.json')
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Created Jellyfin metadata for {title} at {metadata_path}")
        except PermissionError as e:
            logger.error(f"Permission denied creating metadata: {e}")
            logger.error("Check /downloads volume write permissions in Docker")
        except OSError as e:
            if e.errno == 30:  # Read-only filesystem
                logger.error(f"Read-only filesystem: Cannot create metadata")
                logger.error("Mount /downloads volume with write permissions (remove :ro flag)")
            else:
                logger.error(f"Error creating metadata: {e}")
        except Exception as e:
            logger.error(f"Error creating metadata: {e}")
    
    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Remove invalid characters from filename"""
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        return filename.strip('.')


class DownloadQueue:
    """Manages download queue and background processing"""
    
    def __init__(self):
        self.queue = []
        self.downloader = mangaDownloader()
        self.active_downloads = {}
    
    def add_to_queue(self, manga_info: dict, download_id: int):
        """Add a manga to the download queue"""
        self.queue.append({
            'id': download_id,
            'info': manga_info,
            'added_at': datetime.now(),
            'status': 'queued'
        })
    
    def process_queue(self, on_progress=None):
        """Process downloads in queue"""
        while self.queue:
            item = self.queue.pop(0)
            download_id = item['id']
            manga_info = item['info']
            
            self.active_downloads[download_id] = 'processing'
            
            # Download manga and update progress
            result = self.downloader.download_manga(manga_info, progress_callback=on_progress)
            
            # Result would be used to update download status in API
            del self.active_downloads[download_id]


# Global downloader instance
_downloader = None
_queue = None


def get_downloader():
    """Get or create downloader instance"""
    global _downloader
    if _downloader is None:
        _downloader = mangaDownloader()
    return _downloader


def get_queue():
    """Get or create download queue instance"""
    global _queue
    if _queue is None:
        _queue = DownloadQueue()
    return _queue
