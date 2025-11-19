"""
Enhanced manga Downloader Module - HakuNeko Integration
Handles downloading manga from direct sources and converting to CBZ format with proper metadata
Based on HakuNeko's architecture for CBZ creation and file organization
"""

import os
import json
import requests
import zipfile
import io
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
import logging
import threading
from typing import List, Tuple, Dict, Optional, Callable
import time
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def natural_sort_key(text: str) -> list:
    """
    Generate a key for natural sorting (handles numeric sequences properly)
    Converts: ['page', '2'] instead of alphabetical which would put '10' before '2'
    
    Examples:
        'page1' < 'page2' < 'page10' (not 'page1' < 'page10' < 'page2')
        '/chapter/1.html' < '/chapter/2.html' < '/chapter/10.html'
    """
    def atoi(text):
        return int(text) if text.isdigit() else text.lower()
    
    return [atoi(c) for c in re.split(r'(\d+)', text)]


class ComicInfoGenerator:
    """
    Generates ComicInfo.xml metadata for CBZ files
    Based on HakuNeko's ComicInfoGenerator implementation
    ComicInfo.xml is the standard metadata format for CBZ/comic book archives
    """
    
    @staticmethod
    def create_comic_info_xml(manga_title: str, chapter_title: str, page_count: int) -> str:
        """
        Create ComicInfo.xml content for CBZ metadata
        This follows the standard ComicInfo.xml schema used by comic readers
        
        Args:
            manga_title: Series title
            chapter_title: Chapter title/name
            page_count: Total number of pages in the chapter
        
        Returns:
            XML string for ComicInfo.xml
        """
        root = ET.Element('ComicInfo')
        root.set('xmlns:xsd', 'http://www.w3.org/2001/XMLSchema')
        root.set('xmlns:xsi', 'http://www.w3.org/2001/XMLSchema-instance')
        
        # Add metadata elements
        title = ET.SubElement(root, 'Title')
        title.text = str(chapter_title)
        
        series = ET.SubElement(root, 'Series')
        series.text = str(manga_title)
        
        number = ET.SubElement(root, 'Number')
        number.text = '1'
        
        count = ET.SubElement(root, 'Count')
        count.text = str(page_count)
        
        # Format XML with proper declaration
        xml_str = ET.tostring(root, encoding='unicode')
        return f'<?xml version="1.0" encoding="utf-8"?>\n{xml_str}'


class mangaDownloader:
    """
    Enhanced manga downloader with HakuNeko integration
    Downloads manga chapters and creates proper CBZ archives with metadata
    """
    
    def __init__(self, base_destination: str = None, connector_manager=None):
        """
        Initialize downloader
        
        Args:
            base_destination: Base path for downloads. Defaults to /downloads/manga
            connector_manager: Optional mangaConnectorManager for using existing connectors
        """
        if base_destination is None:
            base_destination = "/downloads/manga"
        
        self.base_destination = base_destination
        self.connector_manager = connector_manager
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })
        self.request_delay = 0.5  # Rate limiting between requests (HakuNeko default)
    
    def download_manga(self, manga_info: Dict, progress_callback: Optional[Callable] = None) -> Dict:
        """
        Download manga and convert to CBZ format
        
        Directory structure created:
        {destination}/{manga.title}/
            ├── cover.jpg (manga cover image, converted from original format)
            ├── Chapter 001 - {manga.title}.cbz
            ├── Chapter 002 - {manga.title}.cbz
            └── series.json (metadata for media servers)
        
        Args:
            manga_info: {
                'title': str - manga title
                'manga_id': str - Unique identifier
                'source': str - Source (mangafox, omegascans, etc)
                'url': str - manga page URL
                'cover_url': str (optional) - Cover image URL
                'chapters': list (optional) - Specific chapters to download
                'destination': str (optional) - Override default destination
            }
            progress_callback: Callable(percent: int, message: str) - Progress updates
        
        Returns:
            {
                'success': bool,
                'message': str - Status message
                'file_path': str - Series directory path (if successful)
                'chapters_downloaded': int - Number of chapters downloaded
                'error': str - Error message (if failed)
            }
        """
        try:
            # Setup
            destination = manga_info.get('destination', self.base_destination)
            if not destination:
                destination = self.base_destination
            
            title = manga_info.get('title', 'Unknown')
            source = manga_info.get('source', 'unknown')
            url = manga_info.get('url', '')
            
            self._progress(progress_callback, 5, "Initializing download...")
            
            # Create series directory (HakuNeko format: {connector}/{manga_title}/)
            series_dir = os.path.join(destination, self._sanitize_path(title))
            
            try:
                os.makedirs(series_dir, exist_ok=True)
                logger.info(f"Created series directory: {series_dir}")
            except Exception as e:
                error_msg = self._format_permission_error(series_dir, str(e))
                logger.error(error_msg)
                return {'success': False, 'error': error_msg}
            
            self._progress(progress_callback, 15, "Fetching chapter list...")
            
            # Get chapters from source - use provided chapters if available
            chapters = manga_info.get('chapters', [])
            if not chapters:
                chapters = self._fetch_chapters(url, source, manga_info)
            
            if not chapters:
                error_msg = f'No chapters found on {source}'
                logger.error(error_msg)
                return {'success': False, 'error': error_msg}
            
            self._progress(progress_callback, 25, f"Found {len(chapters)} chapters")
            
            # Calculate padding for chapter numbering (HakuNeko approach)
            # If 150 chapters: pad to 3 digits (001, 002, ..., 150)
            # If 15 chapters: pad to 2 digits (01, 02, ..., 15)
            total_chapters = len(chapters)
            padding_width = len(str(int(total_chapters)))
            
            logger.info(f"Total chapters: {total_chapters}, padding width: {padding_width}")
            
            # Download chapters and create CBZ files
            cbz_files = []
            total_pages_downloaded = 0
            total_pages_estimated = len(chapters) * 20  # Rough estimate
            
            for idx, chapter in enumerate(chapters):
                base_progress = 25 + (idx / len(chapters)) * 60
                chapter_num = chapter.get('number', str(idx + 1))
                
                self._progress(progress_callback, base_progress, 
                             f"Downloading chapter {chapter_num}...")
                
                # Add rate limiting (HakuNeko approach)
                if idx > 0:
                    time.sleep(self.request_delay)
                
                # Create a wrapper callback for page-level progress
                def page_progress_callback(current_page, total_pages, page_num):
                    # Calculate progress within this chapter
                    chapter_progress = (current_page / total_pages) if total_pages > 0 else 0
                    chapter_weight = 60 / len(chapters)
                    current_progress = base_progress + (chapter_progress * chapter_weight)
                    
                    self._progress(progress_callback, int(current_progress), 
                                 f"Chapter {chapter_num}: Downloading page {current_page}/{total_pages}")
                
                # Try to use connector if available, otherwise fall back to direct scraping
                if self.connector_manager:
                    images = self._download_chapter_with_connector(
                        source, chapter, chapter_num, page_progress_callback
                    )
                else:
                    # Download chapter images directly
                    images = self._download_chapter(chapter['url'], chapter_num, page_progress_callback)
                
                if images:
                    # Create CBZ file with proper chapter numbering
                    cbz_result = self._create_cbz_file(
                        series_dir, title, chapter_num, 
                        chapter.get('title', f'Chapter {chapter_num}'),
                        images, padding_width
                    )
                    if cbz_result:
                        cbz_files.append(cbz_result)
                else:
                    logger.warning(f"No images downloaded for chapter {chapter_num}")
            
            if not cbz_files:
                error_msg = 'Failed to download any chapters'
                logger.error(error_msg)
                return {'success': False, 'error': error_msg}
            
            self._progress(progress_callback, 87, "Downloading cover image...")
            
            # Download and save cover image (converted to JPG)
            cover_url = manga_info.get('cover_url')
            logger.info(f"Cover URL from manga_info: {cover_url}")
            if cover_url:
                logger.info(f"Attempting to download cover from: {cover_url}")
                cover_success = self._download_and_convert_cover(series_dir, cover_url)
                if cover_success:
                    logger.info(f"Cover image downloaded successfully")
                else:
                    logger.warning(f"Failed to download cover image")
            else:
                logger.warning(f"No cover URL provided in manga_info")
            
            self._progress(progress_callback, 93, "Creating metadata...")
            
            # Create metadata for media servers
            self._create_series_metadata(series_dir, title, source, len(cbz_files))
            
            self._progress(progress_callback, 100, "Download complete!")
            
            return {
                'success': True,
                'message': f'Downloaded {len(cbz_files)} chapters',
                'file_path': series_dir,
                'chapters_downloaded': len(cbz_files)
            }
        
        except Exception as e:
            logger.error(f"Download failed: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def _fetch_chapters(self, url: str, source: str, manga_info: Dict = None) -> List[Dict]:
        """
        Fetch chapter list from manga source
        Uses connector manager if available, otherwise falls back to HTML parsing
        
        Args:
            url: manga URL
            source: Source name (e.g., 'omegascans')
            manga_info: Optional manga info dict with 'id' and other metadata
        
        Returns:
            List of chapter dicts: [
                {'number': '1', 'title': 'Chapter 1', 'url': 'https://...'},
                ...
            ]
        """
        import re
        
        try:
            # First try: Use connector manager if available and source is supported
            if self.connector_manager and source:
                try:
                    logger.info(f"Fetching chapters for {source} using connector manager")
                    connector = self.connector_manager.get_connector(source)
                    if connector:
                        # Create a manga dict for the connector
                        # Use manga_info if provided, otherwise create minimal dict
                        if manga_info:
                            id_val = manga_info.get('manga_id')
                            # Defensive: if id_val is a dict, extract string id
                            if isinstance(id_val, dict):
                                id_val = id_val.get('id') or id_val.get('manga_id') or str(id_val)
                            manga = {
                                'id': str(id_val) if id_val is not None else None,
                                'url': url,
                                'title': manga_info.get('title', 'Unknown')
                            }
                        else:
                            manga = {
                                'id': None,  # Connector will extract from URL
                                'url': url,
                                'title': 'Unknown'
                            }
                        chapters_from_connector = connector.get_chapters(manga)
                        
                        if chapters_from_connector:
                            logger.info(f"Connector found {len(chapters_from_connector)} chapters")
                            # Convert connector format to our format
                            chapters = []
                            for ch in chapters_from_connector:

                                # Extract chapter number from title or URL
                                ch_title = ch.get('title', ch.get('name', 'Unknown'))
                                ch_match = re.search(r'(\d+(?:\.\d+)?)', ch_title)
                                ch_num = ch_match.group(1) if ch_match else str(len(chapters) + 1)

                                # Preserve all fields from connector, especially 'id', but ensure they are strings
                                chapter_dict = {
                                    'number': ch_num,
                                    'title': ch_title,
                                    'url': ch.get('url', url)
                                }
                                # Ensure 'id' is a string if present
                                if 'id' in ch:
                                    id_val = ch['id']
                                    if isinstance(id_val, dict):
                                        id_val = id_val.get('id') or id_val.get('manga_id') or str(id_val)
                                    chapter_dict['id'] = str(id_val)
                                # Ensure 'manga_id' is a string if present
                                if 'manga_id' in ch:
                                    manga_id_val = ch['manga_id']
                                    if isinstance(manga_id_val, dict):
                                        manga_id_val = manga_id_val.get('id') or manga_id_val.get('manga_id') or str(manga_id_val)
                                    chapter_dict['manga_id'] = str(manga_id_val)

                                chapters.append(chapter_dict)
                            
                            if chapters:
                                logger.info(f"Successfully fetched {len(chapters)} chapters using connector")
                                return chapters
                except Exception as e:
                    logger.error(f"Connector fetch failed: {e}", exc_info=True)
                    logger.info(f"Falling back to HTML parsing")
            
            # Fallback: HTML parsing
            self._rate_limit()
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            
            from bs4 import BeautifulSoup
            
            chapters = []
            chapter_dict = {}  # Track unique chapters
            
            # Try to parse with BeautifulSoup for better structure extraction
            try:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Look for chapter links in common structures
                # Pattern: /c{number}/ (FanFox/mangaFox style) - may have additional path like /1.html
                chapter_links = soup.find_all('a', href=re.compile(r'/c(\d+(?:\.\d+)?)', re.IGNORECASE))
                
                if chapter_links:
                    base_domain = '/'.join(response.url.split('/')[:3])  # Get protocol://domain
                    
                    for link in chapter_links:
                        href = link.get('href', '')
                        # Extract chapter number from URL
                        match = re.search(r'/c(\d+(?:\.\d+)?)', href)
                        if match:
                            chapter_num = match.group(1)
                            # Skip if we already have this chapter number
                            if chapter_num in chapter_dict:
                                continue
                            # Build full URL if relative
                            if href.startswith('/'):
                                full_url = base_domain + href
                            elif href.startswith('http'):
                                full_url = href
                            else:
                                # Relative to current URL
                                base_path = '/'.join(response.url.split('/')[:-1])
                                full_url = base_path + '/' + href
                            # Ensure id/manga_id are not dicts (defensive)
                            chapter_dict[chapter_num] = {
                                'number': chapter_num,
                                'title': f'Chapter {chapter_num}',
                                'url': full_url,
                                'id': str(chapter_num),
                                'manga_id': str(chapter_num)
                            }
                    
                    # Convert dict to list and sort
                    chapters = list(chapter_dict.values())
                    chapters.sort(key=lambda x: float(x['number']))
                    
            except ImportError:
                logger.warning("BeautifulSoup not available, falling back to regex")
            
            # Fallback to regex if BeautifulSoup didn't find enough chapters
            if len(chapters) < 1:
                logger.info("BeautifulSoup found < 1 chapters, using regex fallback")
                
                # Pattern: /c{number}/ with possible following content
                pattern1 = r'href=["\']([^"\']*?/c(\d+(?:\.\d+)?)[^"\']*?)["\']'
                
                for match in re.finditer(pattern1, response.text):
                    url_part = match.group(1)
                    chapter_num = match.group(2)
                    
                    if chapter_num in chapter_dict:
                        continue
                    
                    # Build full URL if relative
                    if url_part.startswith('/'):
                        base_domain = '/'.join(response.url.split('/')[:3])
                        full_url = base_domain + url_part
                    else:
                        full_url = url_part
                    
                    chapter_dict[chapter_num] = {
                        'number': chapter_num,
                        'title': f'Chapter {chapter_num}',
                        'url': full_url
                    }
                
                chapters = list(chapter_dict.values())
                chapters.sort(key=lambda x: float(x['number']))
            
            if not chapters:
                # Last resort: treat main URL as chapter 1
                logger.warning(f"No chapters found via HTML parsing, treating URL as single chapter")
                chapters = [{'number': '1', 'title': 'Chapter 1', 'url': url}]
            
            # Defensive: ensure id/manga_id are not dicts
            chapters = [{
                'number': '1',
                'title': 'Chapter 1',
                'url': url,
                'id': '1',
                'manga_id': '1'
            }]
            logger.info(f"Found {len(chapters)} chapters from {source}")
            return chapters
        
        except Exception as e:
            logger.error(f"Error fetching chapters from {source}: {e}", exc_info=True)
            return []
    
    def _download_chapter_with_connector(self, source: str, chapter: Dict, 
                                         chapter_num: str, page_progress_callback=None) -> List[Tuple[str, bytes]]:
        """
        Download chapter using a manga connector (HakuNeko-based)
        This approach uses existing connector logic which is more robust
        For sites like FanFox that return page navigation URLs, we extract images from each page
        
        Args:
            page_progress_callback: Optional callback for page-level progress (current, total, page_num)
        
        Returns:
            List of (filename, image_data) tuples
        """
        try:
            connector = self.connector_manager.get_connector(source)
            if not connector:
                logger.warning(f"No connector for {source}, falling back to direct scraping")
                return self._download_chapter(chapter.get('url', ''), chapter_num, page_progress_callback)
            
            # Get page URLs from connector
            page_urls = connector.get_pages(chapter)
            
            if not page_urls:
                logger.warning(f"No pages found via connector for chapter {chapter_num}")
                return []
            
            logger.info(f"Found {len(page_urls)} pages via connector for chapter {chapter_num}")
            
            # Download images from page URLs
            images = []
            total_pages = len(page_urls)
            failed_pages = []  # Track failed pages
            consecutive_failures = 0  # Track consecutive failures for early stopping
            max_consecutive_failures = 5  # Stop after 5 consecutive failures (for direct images)
            
            # Use natural sort to maintain correct page order (1, 2, 3... not 1, 10, 2)
            for i, page_url in enumerate(sorted(page_urls, key=natural_sort_key)):
                # Report page progress
                if page_progress_callback:
                    page_progress_callback(i + 1, total_pages, i + 1)
                
                # Extract page number from API URL for logging
                import re
                page_match = re.search(r'page=(\d+)', page_url)
                api_page_num = page_match.group(1) if page_match else 'unknown'
                logger.info(f"→ Processing API page={api_page_num} (index {i}) -> will save as {str(i + 1).zfill(4)}.xxx")
                
                # Try downloading page with retries
                max_retries = 3
                retry_delay = 1  # seconds
                success = False
                
                for attempt in range(max_retries):
                    try:
                        # Handle relative URLs
                        if page_url.startswith('/'):
                            base_domain = '/'.join(chapter.get('url', '').split('/')[:3])
                            if not base_domain.startswith('http'):
                                base_domain = 'https://' + base_domain
                            page_url = base_domain + page_url
                        
                        self._rate_limit()
                        
                        # For API calls (chapterfun.ashx), add required headers
                        headers = {}
                        if 'chapterfun.ashx' in page_url:
                            headers = {
                                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                                'Accept': '*/*',
                                'Accept-Language': 'en-US,en;q=0.9',
                                'Accept-Encoding': 'gzip, deflate, br',
                                'X-Requested-With': 'XMLHttpRequest',
                                'Referer': chapter.get('url', page_url),
                            }
                        
                        page_response = self.session.get(page_url, timeout=10, headers=headers)
                        
                        if page_response.status_code == 200:
                            content_type = page_response.headers.get('content-type', '').lower()
                            
                            # Check if this is a direct image URL (mangaFox, etc.)
                            if 'image/' in content_type:
                                # Direct image download - no need to parse HTML/JS
                                ext = self._get_image_extension(page_url, content_type)
                                page_num = str(len(images) + 1).zfill(4)  # Use actual image count
                                filename = f"{page_num}.{ext}"
                                images.append((filename, page_response.content))
                                logger.info(f"✓ Downloaded page {i+1}/{len(page_urls)}: {filename} (direct image)")
                                success = True
                                consecutive_failures = 0  # Reset failure counter
                                break  # Got image, move to next page
                            
                            # Check if this is packed JavaScript (FanFox API response)
                            is_js = 'eval(function(p,a,c,k,e,d)' in page_response.text or 'application/javascript' in content_type
                            
                            if is_js:
                                # Page response is packed JavaScript - unpack it to get image URLs
                                img_urls = self._extract_image_urls_from_packed_js(page_response.text)
                                
                                logger.info(f"  Extracted {len(img_urls)} image URL(s) from API page={api_page_num}")
                                
                                if img_urls:
                                    # mangaFox API returns pvalue array with [prev_page, current_page]
                                    # For first API call (page=1): use pvalue[0] to get 000.jpg (first page)
                                    # For all subsequent calls: use pvalue[1] to get the current page
                                    # This avoids downloading duplicate images
                                    
                                    if i == 0 and len(img_urls) >= 2:
                                        # First page: download BOTH images (000.jpg and 001.jpg)
                                        selected_urls = img_urls[:2]
                                        logger.info(f"  First API call: downloading {len(selected_urls)} images (including 000.jpg)")
                                    elif len(img_urls) >= 2:
                                        # Subsequent pages: only download pvalue[1] (current page)
                                        selected_urls = [img_urls[1]]
                                        logger.info(f"  Subsequent call: downloading pvalue[1] only")
                                    else:
                                        # Fallback: download whatever we got
                                        selected_urls = img_urls
                                    
                                    # Download selected image(s)
                                    for img_url in selected_urls:
                                        try:
                                            # Make sure URL is absolute
                                            if img_url.startswith('//'):
                                                img_url = 'https:' + img_url
                                            elif not img_url.startswith('http'):
                                                img_url = 'https://' + img_url
                                            
                                            self._rate_limit()
                                            img_response = self.session.get(img_url, timeout=10, headers={
                                                'Referer': page_url,
                                                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                                            })
                                            
                                            if img_response.status_code == 200:
                                                ext = self._get_image_extension(img_url, img_response.headers.get('content-type', ''))
                                                page_num = str(i + 1).zfill(4)
                                                filename = f"{page_num}.{ext}"
                                                images.append((filename, img_response.content))
                                                logger.info(f"✓ Downloaded page {i+1}/{len(page_urls)}: {filename} from API URL index {i} -> {img_url[:80]}...")
                                                success = True
                                                consecutive_failures = 0  # Reset failure counter
                                                break  # Got image, move to next page
                                            else:
                                                logger.warning(f"Failed to download image from API page {i+1}: Status {img_response.status_code}")
                                        except Exception as img_e:
                                            logger.warning(f"Failed to download image from API: {img_e}")
                                    
                                    if not img_urls:
                                        logger.warning(f"Could not extract image URLs from API response for page {i+1}")
                                else:
                                    logger.warning(f"Could not extract image URLs from API response for page {i+1}")
                            else:
                                # Try to extract from HTML page
                                img_url = self._extract_image_from_page_html(page_response.text, page_url)
                                if img_url:
                                    self._rate_limit()
                                    img_response = self.session.get(img_url, timeout=10, headers={
                                        'Referer': page_url,
                                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                                    })
                                    if img_response.status_code == 200:
                                        ext = self._get_image_extension(img_url, img_response.headers.get('content-type', ''))
                                        page_num = str(i + 1).zfill(4)
                                        filename = f"{page_num}.{ext}"
                                        images.append((filename, img_response.content))
                                        logger.debug(f"Downloaded page {i+1}/{len(page_urls)} (attempt {attempt + 1})")
                                        success = True
                                        consecutive_failures = 0  # Reset failure counter
                                    else:
                                        logger.warning(f"Failed to download image from page {i+1}: Status {img_response.status_code}")
                                else:
                                    logger.warning(f"Could not extract image URL from page {i+1}")
                            
                            if success:
                                break  # Successfully downloaded, move to next page
                        else:
                            logger.warning(f"Failed to fetch page {i+1}: Status {page_response.status_code}")
                            consecutive_failures += 1
                    
                    except Exception as e:
                        logger.warning(f"Failed to download page {i+1} (attempt {attempt + 1}): {e}")
                        if attempt < max_retries - 1:
                            time.sleep(retry_delay)
                            retry_delay *= 2  # Exponential backoff
                        consecutive_failures += 1
                
                if not success:
                    failed_pages.append((i + 1, page_url))
                    logger.error(f"Failed to download page {i+1} after {max_retries} attempts")
                    
                    # For direct image URLs, stop early on consecutive failures
                    if consecutive_failures >= max_consecutive_failures:
                        logger.info(f"Stopping download after {consecutive_failures} consecutive failures")
                        break
            
            logger.info(f"Successfully downloaded {len(images)}/{total_pages} images from chapter {chapter_num}")
            
            if failed_pages:
                logger.warning(f"Failed to download {len(failed_pages)} pages: {[p[0] for p in failed_pages]}")
            
            return images
        
        except Exception as e:
            logger.error(f"Error downloading chapter with connector: {e}")
            # Fall back to direct scraping
            return self._download_chapter(chapter.get('url', ''), chapter_num, page_progress_callback)
    
    def _unpack_fanfox_javascript(self, packed_js: str) -> List[str]:
        """
        Unpack FanFox's Dean Edwards packed JavaScript and extract image URLs from pvalue array
        
        FanFox uses JavaScript packing to obfuscate the image loading code.
        The packed format is: eval(function(p,a,c,k,e,d){...}('code',a,c,'dict',e,d))
        The API returns a pvalue array with 1-2 images per call.
        
        Returns:
            List of image URLs (usually 2: [previous_page, current_page])
        """
        try:
            import re
            
            # Extract the packed code parameters
            # Pattern: }('encoded_code',a_val,c_val,'dict_val') - NO trailing comma
            pattern = r"\}\('([^']+)',(\d+),(\d+),'([^']*)'"
            match = re.search(pattern, packed_js)
            
            if not match:
                logger.warning(f"Could not match unpack pattern in response")
                return None
            
            p_code = match.group(1)  # Encoded code
            k_str = match.group(4)  # Dictionary string
            k_array = k_str.split('|')
            
            logger.debug(f"Unpacking FanFox JavaScript with {len(k_array)} dictionary entries")
            logger.debug(f"First 10 dict entries: {k_array[:10]}")
            
            # Unpack: replace each encoded index with dictionary value
            result = p_code
            
            # Replace indices - process all dictionary entries
            for i in range(len(k_array)):
                if k_array[i]:
                    if i < 10:
                        # Single digit (0-9)
                        result = re.sub(r'\b' + str(i) + r'\b', k_array[i], result)
                    else:
                        # Base-36 character (a-z for 10-35)
                        char = chr(ord('a') + i - 10)
                        result = re.sub(r'\b' + char + r'\b', k_array[i], result)
            
            logger.debug(f"Unpacked JavaScript (first 200 chars): {result[:200]}")
            
            # Extract ALL image URLs from pvalue array
            # mangaFox dm5imagefun() pattern: var pix="base"; var pvalue=["/rel1","/rel2"];
            # The API returns 2 images: pvalue[0] (previous page), pvalue[1] (current page)
            
            # Extract base CDN path (pix variable) and pvalue array
            base_match = re.search(r'var\s+pix\s*=\s*["\']([^"\']+)["\']', result)
            pvalue_match = re.search(r'pvalue\s*=\s*\[([^\]]+)\]', result)
            
            if base_match and pvalue_match:
                base_url = base_match.group(1)
                pvalue_content = pvalue_match.group(1)
                
                # Extract all relative paths from pvalue array
                rel_paths = re.findall(r'["\']([^"\']+\.(?:jpg|png|webp|gif)[^"\']*)["\']', pvalue_content)
                
                if rel_paths:
                    # Construct full URLs for all images in pvalue array
                    full_urls = []
                    for rel_path in rel_paths:
                        full_url = base_url + rel_path
                        
                        # Ensure absolute URL - add https: if starts with //
                        if full_url.startswith('//'):
                            full_url = 'https:' + full_url
                        elif not full_url.startswith('http'):
                            # Doesn't have protocol, add it
                            full_url = 'https://' + full_url
                        
                        full_urls.append(full_url)
                    
                    logger.debug(f"Extracted {len(full_urls)} images from pvalue array")
                    return full_urls
            
            logger.warning("Could not extract pvalue array from unpacked JavaScript")
            logger.warning(f"Unpacked code: {result}")
            return []
            
        except Exception as e:
            logger.error(f"Error unpacking FanFox JavaScript: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def _extract_image_urls_from_packed_js(self, packed_js: str) -> List[str]:
        """
        Extract all image URLs from packed JavaScript
        Returns list of image URLs from the pvalue array (typically 1-2 URLs)
        """
        try:
            import re
            
            urls = self._unpack_fanfox_javascript(packed_js)
            return urls if urls else []
            
        except Exception as e:
            logger.error(f"Error extracting image URLs from packed JS: {e}")
            return []
        """
        Extract actual image URL from a page HTML
        Handles FanFox and similar sites that load images via JavaScript/API
        
        Returns:
            Image URL or None if not found
        """
        try:
            from bs4 import BeautifulSoup
            import re
            
            soup = BeautifulSoup(html, 'html.parser')
            
            # Try method 1: Direct img tags with mfcdn (FanFox CDN)
            img_tags = soup.find_all('img', src=re.compile(r'mfcdn'))
            if img_tags:
                src = img_tags[0].get('src')
                if src:
                    if src.startswith('//'):
                        return 'https:' + src
                    return src
            
            # Try method 2: Extract image from FanFox's packed JavaScript API
            # FanFox calls chapterfun.ashx which returns packed JavaScript
            # We need to unpack it to get the image URL
            script_tags = soup.find_all('script')
            for script in script_tags:
                text = script.string if script.string else ""
                
                # Look for Dean Edwards packed JavaScript pattern
                if 'eval(function(p,a,c,k,e,d)' in text:
                    img_url = self._unpack_fanfox_javascript(text)
                    if img_url:
                        return img_url
                
                # Look for pattern: d = ["url", ...]
                match = re.search(r'd\s*=\s*\[\s*["\']([^"\']+mfcdn[^"\']*)["\']', text)
                if match:
                    img_url = match.group(1)
                    if img_url.startswith('//'):
                        return 'https:' + img_url
                    return img_url
            
            # Try method 3: Look for any image URL in mfcdn domain
            mfcdn_urls = re.findall(r'https?://[^"\s<>]*mfcdn[^"\s<>]*\.(?:jpg|png|webp|gif)', html)
            if mfcdn_urls:
                return mfcdn_urls[0]
            
            # Try method 4: Look for lazy-loaded image attributes
            img_elements = soup.find_all(['img', 'picture', 'source'], attrs={'data-src': True})
            for elem in img_elements:
                data_src = elem.get('data-src')
                if data_src and 'mfcdn' in data_src:
                    if data_src.startswith('//'):
                        return 'https:' + data_src
                    return data_src
            
            logger.warning(f"Could not extract image URL from {page_url}")
            return None
            
        except Exception as e:
            logger.error(f"Error extracting image from page HTML: {e}")
            return None
    
    
    def _download_chapter(self, url: str, chapter_num: str, page_progress_callback=None) -> List[Tuple[str, bytes]]:
        """
        Download all images from a chapter page
        
        Args:
            page_progress_callback: Optional callback for page-level progress (current, total, page_num)
        
        Returns:
            List of (filename, image_data) tuples
            Filenames are padded: 0001.jpg, 0002.jpg, etc.
        """
        try:
            self._rate_limit()
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            
            import re
            
            # Extract image URLs from page
            # Support multiple patterns for different sites
            image_urls = set()
            
            # Pattern 1: data-src attributes (lazy loading)
            pattern1 = r'data-src\s*=\s*["\']([^"\']+\.(?:jpg|jpeg|png|gif|webp))'
            image_urls.update(re.findall(pattern1, response.text, re.IGNORECASE))
            
            # Pattern 2: src attributes
            pattern2 = r'src\s*=\s*["\']([^"\']+\.(?:jpg|jpeg|png|gif|webp))'
            image_urls.update(re.findall(pattern2, response.text, re.IGNORECASE))
            
            # Pattern 3: URLs in JSON/data attributes
            pattern3 = r'["\']?(https?://[^"\'<>\s]+\.(?:jpg|jpeg|png|gif|webp))'
            image_urls.update(re.findall(pattern3, response.text, re.IGNORECASE))
            
            # Filter out thumbnails and logos
            image_urls = [
                url for url in image_urls 
                if not any(x in url.lower() for x in ['thumb', 'logo', 'avatar', 'icon', 'banner'])
            ]
            
            if not image_urls:
                logger.warning(f"No images found in chapter {chapter_num}")
                return []
            
            logger.info(f"Found {len(image_urls)} images in chapter {chapter_num}")
            
            # Download images
            images = []
            failed_pages = []  # Track failed pages
            total_pages = len(image_urls)
            
            # Use natural sort to maintain correct page order (1, 2, 3... not 1, 10, 2)
            for i, img_url in enumerate(sorted(image_urls, key=natural_sort_key)):
                # Report page progress
                if page_progress_callback:
                    page_progress_callback(i + 1, total_pages, i + 1)
                
                # Try downloading with retries
                max_retries = 3
                retry_delay = 1
                success = False
                
                for attempt in range(max_retries):
                    try:
                        self._rate_limit()
                        img_response = self.session.get(img_url, timeout=10)
                        
                        if img_response.status_code == 200:
                            # Determine extension from URL or content-type
                            ext = self._get_image_extension(img_url, img_response.headers.get('content-type', ''))
                            
                            # Use 4-digit padding for page numbers (HakuNeko standard)
                            page_num = str(i + 1).zfill(4)
                            filename = f"{page_num}.{ext}"
                            
                            images.append((filename, img_response.content))
                            logger.debug(f"Downloaded page {i+1}/{len(image_urls)} (attempt {attempt + 1})")
                            success = True
                            break
                    
                    except Exception as e:
                        logger.warning(f"Failed to download image {i+1} (attempt {attempt + 1}): {e}")
                        if attempt < max_retries - 1:
                            import time
                            time.sleep(retry_delay)
                            retry_delay *= 2  # Exponential backoff
                
                if not success:
                    failed_pages.append((i + 1, img_url))
                    logger.error(f"Failed to download page {i+1} after {max_retries} attempts")
            
            logger.info(f"Successfully downloaded {len(images)} images from chapter {chapter_num}")
            return images
        
        except Exception as e:
            logger.error(f"Error downloading chapter {chapter_num}: {e}")
            return []
    
    def _create_cbz_file(self, series_dir: str, manga_title: str, chapter_num: str, 
                         chapter_title: str, images: List[Tuple[str, bytes]], 
                         padding_width: int) -> Optional[str]:
        """
        Create a CBZ file from chapter images
        CBZ is a ZIP archive with images and ComicInfo.xml metadata
        
        Returns:
            Path to created CBZ file if successful, None otherwise
        """
        if not images:
            logger.warning(f"No images to create CBZ for chapter {chapter_num}")
            return None
        
        try:
            # Format chapter number with proper padding
            try:
                chapter_int = int(float(chapter_num))
                chapter_padded = f"{chapter_int:0{padding_width}d}"
            except (ValueError, TypeError):
                chapter_padded = chapter_num
            
            # CBZ filename format: Chapter XXX - {mangaTitle}.cbz
            cbz_filename = f"Chapter {chapter_padded} - {self._sanitize_filename(manga_title)}.cbz"
            cbz_path = os.path.join(series_dir, cbz_filename)
            
            logger.info(f"Creating CBZ: {cbz_path}")
            
            # Create ZIP archive (CBZ is a ZIP file)
            # Use STORE method for compatibility (no compression - faster, more compatible)
            with zipfile.ZipFile(cbz_path, 'w', zipfile.ZIP_STORED) as cbz:
                # Add ComicInfo.xml metadata (standard for CBZ)
                comic_info_xml = ComicInfoGenerator.create_comic_info_xml(
                    manga_title,
                    chapter_title,
                    len(images)
                )
                cbz.writestr('ComicInfo.xml', comic_info_xml)
                logger.debug(f"Added ComicInfo.xml to CBZ")
                
                # Add image files to archive
                for filename, image_data in images:
                    cbz.writestr(filename, image_data)
                    logger.debug(f"Added {filename} to CBZ")
            
            file_size_mb = os.path.getsize(cbz_path) / (1024 * 1024)
            logger.info(f"Created CBZ successfully: {cbz_filename} ({file_size_mb:.2f} MB)")
            
            return cbz_path
        
        except PermissionError as e:
            logger.error(f"Permission denied creating CBZ {cbz_path}: {e}")
            logger.error("Check write permissions on /downloads volume")
            return None
        except OSError as e:
            if e.errno == 30:  # Read-only filesystem
                logger.error(f"Read-only filesystem: {cbz_path}")
                logger.error("Mount /downloads with write permissions (remove :ro)")
                return None
            logger.error(f"OS error creating CBZ {cbz_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error creating CBZ for chapter {chapter_num}: {e}")
            # Clean up partial file
            if os.path.exists(cbz_path):
                try:
                    os.remove(cbz_path)
                except:
                    pass
            return None
    
    def _download_and_convert_cover(self, series_dir: str, cover_url: str) -> bool:
        """
        Download manga cover image and save as cover.jpg
        Converts any format (PNG, WebP, etc.) to JPG
        
        Returns:
            True if successful, False otherwise
        """
        try:
            self._rate_limit()
            
            # Add headers to bypass mangaFox/CDN restrictions
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://fanfox.net/',
                'DNT': '1'
            }
            
            response = self.session.get(cover_url, timeout=10, headers=headers)
            response.raise_for_status()
            
            try:
                from PIL import Image
                import io
                
                # Open image from response
                img = Image.open(io.BytesIO(response.content))
                
                # Convert to RGB (remove transparency, handle different modes)
                if img.mode in ('RGBA', 'LA', 'P'):
                    # Create white background
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    
                    # Paste image with alpha channel
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    
                    background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                    img = background
                elif img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # Save as cover.jpg
                cover_path = os.path.join(series_dir, 'cover.jpg')
                img.save(cover_path, 'JPEG', quality=95)
                
                file_size_kb = os.path.getsize(cover_path) / 1024
                logger.info(f"Saved cover image: {cover_path} ({file_size_kb:.2f} KB)")
                return True
            
            except ImportError:
                # PIL not available, save raw bytes
                logger.warning("PIL not available, saving cover as-is")
                cover_path = os.path.join(series_dir, 'cover.jpg')
                with open(cover_path, 'wb') as f:
                    f.write(response.content)
                logger.info(f"Saved cover image (raw): {cover_path}")
                return True
        
        except Exception as e:
            logger.warning(f"Failed to download/convert cover image: {e}")
            return False
    
    def _create_series_metadata(self, series_dir: str, title: str, source: str, 
                                chapter_count: int):
        """Create series.json metadata file for media servers"""
        try:
            metadata = {
                'name': title,
                'source': source,
                'type': 'manga',
                'dateAdded': datetime.now().isoformat(),
                'provider': 'nukedown',
                'chaptersDownloaded': chapter_count
            }
            
            metadata_path = os.path.join(series_dir, 'series.json')
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Created metadata: {metadata_path}")
        
        except Exception as e:
            logger.warning(f"Failed to create metadata: {e}")
    
    @staticmethod
    def _get_image_extension(url: str, content_type: str) -> str:
        """Determine image file extension from URL or content-type"""
        # Check content-type first
        content_type_lower = content_type.lower()
        if 'png' in content_type_lower:
            return 'png'
        elif 'gif' in content_type_lower:
            return 'gif'
        elif 'webp' in content_type_lower:
            return 'webp'
        elif 'jpeg' in content_type_lower or 'jpg' in content_type_lower:
            return 'jpg'
        
        # Fall back to URL extension
        url_lower = url.lower().split('?')[0]  # Remove query params
        if url_lower.endswith('.png'):
            return 'png'
        elif url_lower.endswith('.gif'):
            return 'gif'
        elif url_lower.endswith('.webp'):
            return 'webp'
        elif url_lower.endswith('.jpg') or url_lower.endswith('.jpeg'):
            return 'jpg'
        
        # Default to jpg
        return 'jpg'
    
    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Remove invalid filesystem characters from filename"""
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        return filename.strip('. ')
    
    @staticmethod
    def _sanitize_path(path_str: str) -> str:
        """Sanitize path component"""
        # For path, also remove leading/trailing spaces
        return mangaDownloader._sanitize_filename(path_str)
    
    def _rate_limit(self):
        """Implement rate limiting (HakuNeko approach)"""
        time.sleep(self.request_delay)
    
    @staticmethod
    def _progress(callback: Optional[Callable], percent: int, message: str):
        """Send progress update"""
        if callback:
            try:
                callback(percent, message)
            except Exception as e:
                # Re-raise cancellation exceptions
                if "cancelled" in str(e).lower():
                    raise e
                logger.warning(f"Progress callback error: {e}")
    
    @staticmethod
    def _format_permission_error(path: str, error: str) -> str:
        """Format helpful permission error message"""
        return (
            f"Cannot access {path}\n"
            f"Error: {error}\n\n"
            f"Troubleshooting:\n"
            f"1. Check /downloads volume is mounted with write permissions\n"
            f"2. In docker-compose.yml: volumes:\n"
            f"      - ./downloads:/downloads  (NOT ./downloads:/downloads:ro)\n"
            f"3. Verify container user has write permissions\n"
            f"4. Ensure /downloads directory exists on host"
        )
    
    def _download_image_with_browser(self, image_url: str, referer: str) -> bytes:
        """
        Download image using Selenium browser automation (for Cloudflare bypass)
        
        Args:
            image_url: The image URL to download
            referer: Referer header to use
            
        Returns:
            Image data as bytes
        """
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager
            import base64
            import time
            
            # Set up Chrome options
            chrome_options = Options()
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            chrome_options.add_argument('--disable-images')  # Don't load images except the one we want
            chrome_options.add_argument('--disable-javascript')  # Disable JS for security
            
            # Initialize WebDriver
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            
            try:
                # Navigate to the image URL
                driver.get(image_url)
                time.sleep(2)  # Wait for load
                
                # Get the page source (which should be the image data)
                page_source = driver.page_source
                
                # If it's an image, the page source will contain the binary data
                # But we need to get it properly
                script = """
                var img = document.querySelector('img');
                if (img) {
                    var canvas = document.createElement('canvas');
                    var ctx = canvas.getContext('2d');
                    canvas.width = img.naturalWidth;
                    canvas.height = img.naturalHeight;
                    ctx.drawImage(img, 0, 0);
                    return canvas.toDataURL('image/png').split(',')[1];
                }
                return null;
                """
                
                # Execute script to get image data
                b64_data = driver.execute_script(script)
                
                if b64_data:
                    # Decode base64 image data
                    image_data = base64.b64decode(b64_data)
                    logger.debug(f"Downloaded image with browser: {len(image_data)} bytes")
                    return image_data
                else:
                    # Fallback: try to get the response directly
                    logger.warning("Browser script failed, trying direct request")
                    response = self.session.get(image_url, headers={'Referer': referer}, timeout=30)
                    response.raise_for_status()
                    return response.content
                
            finally:
                driver.quit()
                
        except ImportError:
            logger.warning("Selenium not available for browser download")
            raise Exception("Selenium not available")
        except Exception as e:
            logger.error(f"Browser download failed: {e}")
            raise


class DownloadQueue:
    """Manages background download queue processing"""
    
    def __init__(self, base_destination: str = None):
        self.queue: List[Dict] = []
        self.downloader = mangaDownloader(base_destination)
        self.active_downloads: Dict[int, str] = {}
        self.download_results: Dict[int, Dict] = {}
        self.lock = threading.Lock()
    
    def add_to_queue(self, manga_info: Dict, download_id: int) -> bool:
        """Add manga to download queue"""
        with self.lock:
            self.queue.append({
                'id': download_id,
                'info': manga_info,
                'added_at': datetime.now(),
                'status': 'queued'
            })
            logger.info(f"Added download {download_id} to queue")
            return True
    
    def get_queue_status(self) -> Dict:
        """Get current queue status"""
        with self.lock:
            return {
                'queued': len(self.queue),
                'active': len(self.active_downloads),
                'queue': [
                    {'id': item['id'], 'title': item['info'].get('title')}
                    for item in self.queue
                ],
                'active': list(self.active_downloads.keys())
            }
    
    def get_download_result(self, download_id: int) -> Optional[Dict]:
        """Get result of completed download"""
        with self.lock:
            return self.download_results.get(download_id)


# Global instances
_downloader_instance = None
_queue_instance = None


def get_downloader(base_destination: str = None, connector_manager=None) -> mangaDownloader:
    """Get or create downloader instance"""
    global _downloader_instance
    if _downloader_instance is None:
        _downloader_instance = mangaDownloader(base_destination, connector_manager)
    return _downloader_instance


def get_queue(base_destination: str = None, connector_manager=None) -> DownloadQueue:
    """Get or create download queue instance"""
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = DownloadQueue(base_destination)
        # Optionally set connector manager on the downloader
        if connector_manager:
            _queue_instance.downloader.connector_manager = connector_manager
    return _queue_instance
