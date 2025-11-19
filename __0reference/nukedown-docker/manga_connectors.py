#!/usr/bin/env python3

"""
manga Site Connectors for nukedown
Based on HakuNeko connector architecture
Implements mangaFox and OmegaScans (HeanCms) connectors
"""

import requests
import json
import time
import logging
from urllib.parse import urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup
import re
from typing import List, Dict, Optional, Any

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class mangaConnector:
    """Base class for manga site connectors"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })
        self.request_delay = 0.5  # Reduced delay for faster scraping
        
    def _make_request(self, url: str, **kwargs) -> requests.Response:
        """Make HTTP request with retry logic and optimized timeout"""
        time.sleep(self.request_delay)  # Rate limiting
        try:
            # Optimized timeout: 5 seconds connect, 10 seconds read
            timeout = kwargs.pop('timeout', (5, 10))
            response = self.session.get(url, timeout=timeout, **kwargs)
            response.raise_for_status()
            return response
        except requests.Timeout:
            logger.warning(f"Request timeout for {url}")
            raise
        except requests.RequestException as e:
            logger.error(f"Request failed for {url}: {e}")
            raise
            
    def get_mangas(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get list of manga from the site"""
        raise NotImplementedError
        
    def search_manga(self, query: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Search for manga by title"""
        raise NotImplementedError
        
    def get_chapters(self, manga: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get chapters for a specific manga"""
        raise NotImplementedError
        
    def get_pages(self, chapter: Dict[str, Any]) -> List[str]:
        """Get page image URLs for a specific chapter"""
        raise NotImplementedError
    
    def get_manga_details(self, manga: Dict[str, Any]) -> Dict[str, Any]:
        """Get detailed information about a manga including cover image"""
        raise NotImplementedError


class mangaFoxConnector(mangaConnector):
    """mangaFox connector based on simplified direct URL approach"""
    
    def __init__(self):
        super().__init__()
        self.base_url = 'https://mangahub.us'
        self.id = 'mangafox'
        self.label = 'mangaFox'
        self.tags = ['manga', 'english']
        
        # Set adult cookie
        self.session.cookies.set('isAdult', '1')
        self.session.headers.update({
            'x-cookie': 'isAdult=1'
        })
        
    def get_mangas(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get manga by searching for a broad query to get all available manga"""
        try:
            # Since search returns the same results regardless of query, just search once
            # and get all available manga from the site
            manga_list = []
            seen_titles = set()
            
            # Use a broad search to get all manga
            results = self.search_manga('manga', limit=None)  # No limit to get all
            
            for manga in results:
                if manga['title'] not in seen_titles:
                    manga_list.append(manga)
                    seen_titles.add(manga['title'])
                    
                    if limit and len(manga_list) >= limit:
                        break
            
            logger.info(f"Retrieved {len(manga_list)} manga from site")
            return manga_list
            
        except Exception as e:
            logger.error(f"Error getting manga list: {e}")
            return []
            
    def _get_mangas_from_page(self, page: int) -> List[Dict[str, Any]]:
        """Get manga from a specific directory page"""
        page_url = urljoin(self.base_url, f'/directory/{page}.htm')
        response = self._make_request(page_url)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        manga_list = []
        manga_elements = soup.select('div.manga-list-1 ul li p.manga-list-1-item-title a')
        
        for element in manga_elements:
            try:
                title = element.get('title', '').strip()
                link = element.get('href', '')
                
                if title and link:
                    # Extract just the slug for manga_id (e.g., "title_name" from "/manga/title_name/")
                    slug = link.lstrip('/').rstrip('/').split('/')[-1]  # Get last component after removing slashes
                    manga_list.append({
                        'id': slug,  # Use clean slug instead of full path
                        'title': title,
                        'source': self.label,
                        'url': urljoin(self.base_url, link),
                        'type': 'manga',
                        'language': 'english'
                    })
            except Exception as e:
                logger.error(f"Error parsing manga element: {e}")
                continue
                
        return manga_list
        
    def search_manga(self, query: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Search for manga on mangaHub"""
        try:
            # Try the correct search URL first
            search_url = f"{self.base_url}/search?q={query.replace(' ', '+')}"
            response = self._make_request(search_url)
            soup = BeautifulSoup(response.content, 'html.parser')

            results = []
            seen_urls = set()
            query_lower = query.lower()

            # Look for manga links in search results
            manga_links = soup.find_all('a', href=lambda x: x and '/manga/' in x and x != '/manga')

            # Score and sort results by relevance
            scored_results = []

            for link in manga_links:
                href = link.get('href', '')
                text = link.get_text(strip=True)

                # Skip if we've already seen this URL
                if href in seen_urls:
                    continue
                seen_urls.add(href)

                # Extract manga name from URL
                manga_name = href.split('/manga/')[-1].split('/')[0]

                # Use text if available, otherwise create title from URL
                if text and len(text) > 2:
                    title = text
                else:
                    # Convert URL slug to title case
                    title = manga_name.replace('-', ' ').title()

                # Extract genre tags from the parent element
                genres = []
                parent = link.parent if link.parent else link.find_parent()
                if parent:
                    # Look for genre links in the parent container
                    genre_links = parent.find_all('a', href=lambda x: x and '/genre/' in x)
                    genres = [genre_link.get_text(strip=True).lower() for genre_link in genre_links]

                if manga_name and title:
                    # Calculate relevance score
                    title_lower = title.lower()
                    score = 0

                    # Exact match
                    if title_lower == query_lower:
                        score += 100
                    # Starts with query
                    elif title_lower.startswith(query_lower):
                        score += 50
                    # Contains query
                    elif query_lower in title_lower:
                        score += 20
                    # URL contains query
                    elif query_lower in manga_name.lower():
                        score += 10

                    if score > 0:
                        manga = {
                            'id': manga_name,
                            'title': title,
                            'source': self.label,
                            'url': urljoin(self.base_url, href),
                            'type': 'manga',
                            'language': 'english',
                            'genres': genres,  # Add genre tags
                            'relevance_score': score
                        }

                        scored_results.append(manga)

            # Sort by relevance score and limit results
            scored_results.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
            results = scored_results[:limit] if limit else scored_results

            logger.info(f"Found {len(results)} manga matching '{query}'")
            return results

        except Exception as e:
            logger.error(f"Error searching manga: {e}")
            return []
            
    def get_chapters(self, manga: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get chapters for a specific manga using simplified approach"""
        try:
            manga_url = manga.get('url') or urljoin(self.base_url, f"/manga/{manga['id']}")
            manga_name = manga['id']  # Use the manga ID as the name
            
            response = self._make_request(manga_url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            chapters = []
            chapter_urls = {}  # Track multiple URLs per chapter number
            chapter_links = soup.find_all('a', href=True)
            
            for a in chapter_links:
                href = a['href']
                if '/chapter/' in href and manga_name in href:
                    # Extract chapter number first
                    match = re.search(r'/chapter-(\d+)', href)
                    if not match:
                        continue
                        
                    chapter_num = int(match.group(1))
                    
                    if href.startswith('http'):
                        link = href
                    else:
                        link = urljoin(self.base_url, href)
                    
                    # Collect all URLs for this chapter number
                    if chapter_num not in chapter_urls:
                        chapter_urls[chapter_num] = []
                    chapter_urls[chapter_num].append(link)
            
            # Create chapter entries with primary URL and fallbacks
            for chapter_num, urls in chapter_urls.items():
                # Sort URLs by preference (shorter, cleaner URLs first)
                urls.sort(key=lambda x: (len(x), x))
                primary_url = urls[0]
                
                title = f"Chapter {chapter_num}"
                
                # Sanitize title for directory names
                invalid_chars = '<>:"|?*\\'
                for char in invalid_chars:
                    title = title.replace(char, '_')
                
                chapters.append({
                    'id': f"{manga_name}/chapter-{chapter_num}",
                    'name': title,
                    'title': title,
                    'url': primary_url,
                    'fallback_urls': urls[1:] if len(urls) > 1 else [],  # Additional URLs as fallbacks
                    'manga_id': manga.get('id', ''),
                    'chapter_num': chapter_num,
                    'language': 'english'
                })
            
            # Sort by chapter number ascending
            chapters.sort(key=lambda x: x['chapter_num'])
            
            logger.info(f"Found {len(chapters)} chapters for {manga.get('title', 'Unknown')} ({sum(len(ch.get('fallback_urls', [])) for ch in chapters)} fallback URLs available)")
            return chapters
            
        except Exception as e:
            logger.error(f"Error getting chapters: {e}")
            return []
            
    def get_pages(self, chapter: Dict[str, Any]) -> List[str]:
        """Get page URLs for a chapter by determining the exact page count using binary search"""
        try:
            chapter_url = chapter.get('url', '')
            manga_name = chapter.get('manga_id', '')
            
            # Extract chapter number from URL
            match = re.search(r'/chapter-(\d+)', chapter_url)
            if not match:
                logger.warning(f"Could not extract chapter number from {chapter_url}")
                return []
            
            chapter_num = match.group(1)
            
            # First, try to fetch the chapter page to determine actual page count
            total_pages = 0
            try:
                logger.info(f"Fetching chapter page to determine page count: {chapter_url}")
                response = self.session.get(chapter_url, timeout=10)
                response.raise_for_status()
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Try various methods to extract page count from HTML
                total_pages = self._extract_page_count_from_html(soup)
                
                if total_pages > 0:
                    logger.info(f"Successfully determined {total_pages} pages from chapter page")
                else:
                    logger.info("Could not determine page count from HTML, using binary search on image URLs")
                    
            except Exception as e:
                logger.warning(f"Failed to fetch chapter page for page count: {e}")
            
            # If HTML parsing didn't work, use binary search on image URLs
            if total_pages == 0:
                total_pages = self._find_page_count_with_binary_search(manga_name, chapter_num)
            
            # Validate page count
            if total_pages <= 0:
                logger.warning("Could not determine page count, using conservative fallback")
                total_pages = 50  # Very conservative fallback
            elif total_pages > 500:
                logger.warning(f"Unusually high page count ({total_pages}), capping at 500")
                total_pages = 500
            
            # Generate URLs for the determined number of pages
            cdn_domains = [
                'https://imgx.mghcdn.com',  # Primary
                'https://img.mghcdn.com',   # Fallback 1
                'https://img1.mghcdn.com',  # Fallback 2
            ]
            
            for cdn_domain in cdn_domains:
                try:
                    image_urls = []
                    for page_num in range(1, total_pages + 1):
                        url = f'{cdn_domain}/{manga_name}/{chapter_num}/{page_num}.jpg'
                        image_urls.append(url)
                    
                    logger.info(f"Using CDN {cdn_domain} for chapter {chapter.get('title', 'Unknown')} ({len(image_urls)} pages)")
                    return image_urls
                    
                except Exception as cdn_error:
                    logger.warning(f"Failed to use CDN {cdn_domain}: {cdn_error}")
                    continue
            
            # If all CDNs failed, return URLs from primary CDN anyway
            logger.warning("All CDNs failed, using primary CDN as fallback")
            image_urls = []
            for page_num in range(1, total_pages + 1):
                url = f'https://imgx.mghcdn.com/{manga_name}/{chapter_num}/{page_num}.jpg'
                image_urls.append(url)
            
            logger.info(f"Generated {len(image_urls)} page URLs for chapter {chapter.get('title', 'Unknown')} (primary CDN fallback)")
            return image_urls
            
        except Exception as e:
            logger.error(f"Error generating page URLs: {e}")
            return []
    
    def _extract_page_count_from_html(self, soup: BeautifulSoup) -> int:
        """Extract page count from chapter page HTML using various methods"""
        try:
            # Method 1: Look for page navigation/select dropdown
            page_select = soup.select_one('select[name="page"], select.page-select, .page-select select')
            if page_select:
                options = page_select.find_all('option')
                if options:
                    last_option = options[-1]
                    try:
                        page_count = int(last_option.get('value', '0'))
                        if page_count > 0:
                            return page_count
                        return len(options)
                    except (ValueError, TypeError):
                        return len(options)
            
            # Method 2: Look for page navigation links
            page_links = soup.select('a[href*="page="], .page-link, .pagination a')
            page_numbers = []
            for link in page_links:
                href = link.get('href', '')
                page_match = re.search(r'page=(\d+)', href)
                if page_match:
                    page_numbers.append(int(page_match.group(1)))
            
            if page_numbers:
                return max(page_numbers)
            
            # Method 3: Look for image elements
            images = soup.select('img[data-src], img[data-lazy], .chapter-image, .page-image')
            if images:
                return len(images)
            
            # Method 4: Look for JavaScript variables
            script_tags = soup.find_all('script')
            for script in script_tags:
                script_text = script.get_text()
                pages_match = re.search(r'(?:totalPages|pages|pageCount)["\s:]+(\d+)', script_text, re.IGNORECASE)
                if pages_match:
                    return int(pages_match.group(1))
            
            # Method 5: Look for data attributes
            elements_with_data = soup.select('[data-pages], [data-total-pages], [data-image-count]')
            for elem in elements_with_data:
                for attr in ['data-pages', 'data-total-pages', 'data-image-count']:
                    value = elem.get(attr)
                    if value:
                        try:
                            return int(value)
                        except (ValueError, TypeError):
                            continue
            
            return 0
            
        except Exception as e:
            logger.warning(f"Error extracting page count from HTML: {e}")
            return 0
    
    def _find_page_count_with_binary_search(self, manga_name: str, chapter_num: str) -> int:
        """Use binary search to find the exact number of pages in a chapter"""
        try:
            logger.info(f"Using binary search to find page count for {manga_name} chapter {chapter_num}")
            
            # Test with primary CDN
            cdn_domain = 'https://imgx.mghcdn.com'
            
            # Binary search between 1 and 500 pages
            low = 1
            high = 500
            
            # First, find an upper bound by testing powers of 2
            while high <= 1000:  # Max 1000 pages
                test_page = high
                url = f'{cdn_domain}/{manga_name}/{chapter_num}/{test_page}.jpg'
                try:
                    response = self.session.head(url, timeout=5)
                    if response.status_code == 200:
                        # Page exists, try higher
                        low = high
                        high = high * 2
                    else:
                        # Page doesn't exist, search in current range
                        break
                except (requests.Timeout, requests.RequestException):
                    # If we can't connect, assume page doesn't exist
                    break
            
            # Binary search in the determined range
            last_working_page = 0
            while low <= high:
                mid = (low + high) // 2
                url = f'{cdn_domain}/{manga_name}/{chapter_num}/{mid}.jpg'
                
                try:
                    response = self.session.head(url, timeout=5)
                    if response.status_code == 200:
                        last_working_page = mid
                        low = mid + 1
                    else:
                        high = mid - 1
                except (requests.Timeout, requests.RequestException):
                    high = mid - 1
            
            if last_working_page > 0:
                logger.info(f"Binary search found {last_working_page} pages for {manga_name} chapter {chapter_num}")
                return last_working_page
            else:
                logger.warning(f"Binary search found no working pages for {manga_name} chapter {chapter_num}")
                return 0
                
        except Exception as e:
            logger.error(f"Error in binary search for page count: {e}")
            return 0
    
    def get_manga_details(self, manga: Dict[str, Any]) -> Dict[str, Any]:
        """Get detailed information about a manga including cover image"""
        try:
            manga_url = manga.get('url') or urljoin(self.base_url, manga.get('id', ''))
            response = self._make_request(manga_url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            details = {
                'title': manga.get('title', ''),
                'url': manga_url,
                'cover_url': None,
                'description': '',
                'status': '',
                'genres': [],
                'authors': []
            }
            
            # Optimized cover image extraction - check all possible locations
            cover_url = self._extract_cover_mangafox(soup, manga_url)
            details['cover_url'] = cover_url
            
            # Extract description
            desc_elem = soup.select_one('div.manga-content p, div.detail-info .summary')
            if desc_elem:
                details['description'] = desc_elem.get_text(strip=True)
            
            # Extract status
            status_elem = soup.select_one('[class*="status"] span, .detail-info .status')
            if status_elem:
                details['status'] = status_elem.get_text(strip=True)
            
            # Extract genres
            genre_elements = soup.select('.detail-info .genres a, .manga-content .genres a')
            details['genres'] = [elem.get_text(strip=True) for elem in genre_elements]
            
            return details
            
        except Exception as e:
            logger.error(f"Error getting manga details for {manga.get('title')}: {e}")
            return {'title': manga.get('title', ''), 'cover_url': None}
    
    def _extract_cover_mangafox(self, soup, manga_url):
        """Extract cover image with multiple fallback strategies"""
        # Strategy 1: Direct cover image selectors
        cover_selectors = [
            'div.detail-info-cover img',           # Main cover container
            'img.manga-cover-img',
            'img.series-cover',
            'img[src*="cover"][src*="media"]',     # Images with cover in URL
            'div.manga-cover img',
            'img[alt*="cover" i]',                 # Case-insensitive alt text
            'div.detail-info img:first-of-type',   # First image in detail
        ]
        
        for selector in cover_selectors:
            try:
                cover_img = soup.select_one(selector)
                if cover_img:
                    cover_src = cover_img.get('src') or cover_img.get('data-src')
                    if cover_src and len(cover_src) > 10:  # Must be reasonable length
                        cover_url = self._normalize_url(cover_src, self.base_url)
                        if cover_url:
                            logger.debug(f"Found cover via selector '{selector}': {cover_url[:80]}")
                            return cover_url
            except Exception as e:
                logger.debug(f"Selector '{selector}' failed: {e}")
                continue
        
        # Strategy 2: Look for image tags with specific attributes
        all_imgs = soup.find_all('img', limit=50)
        for img in all_imgs:
            src = img.get('src') or img.get('data-src')
            alt = img.get('alt', '').lower()
            
            # Check if this looks like a cover image
            if src and len(src) > 10:
                src_lower = src.lower()
                alt_lower = alt.lower()
                
                # Prioritize images that look like covers
                if any(keyword in src_lower for keyword in ['cover', 'thumb', 'poster', 'mangafox']):
                    if 'avatar' not in src_lower and 'logo' not in src_lower:  # Exclude avatars/logos
                        cover_url = self._normalize_url(src, self.base_url)
                        if cover_url:
                            logger.debug(f"Found cover via attribute scan: {cover_url[:80]}")
                            return cover_url
        
        logger.warning(f"No cover found for {manga_url}")
        return None
    
    def _normalize_url(self, url, base_url):
        """Normalize and validate cover URL"""
        if not url or len(url) < 10:
            return None
        
        try:
            if url.startswith('//'):
                return 'https:' + url
            elif url.startswith('/'):
                return urljoin(base_url, url)
            elif url.startswith('http'):
                return url
            else:
                return None
        except:
            return None


class OmegaScansConnector(mangaConnector):
    """OmegaScans connector based on HeanCms template"""
    
    def __init__(self):
        super().__init__()
        self.base_url = 'https://omegascans.org'
        self.api_url = 'https://api.omegascans.org'
        self.id = 'omegascans'
        self.label = 'OmegaScans'
        self.tags = ['webtoon', 'scanlation', 'english', 'hentai']
        
    def get_mangas(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get all manga from OmegaScans API"""
        manga_list = []
        
        try:
            # Get adult content
            for adult in [True, False]:
                page = 1
                while True:
                    mangas = self._get_mangas_from_page(page, adult)
                    if not mangas:
                        break
                        
                    manga_list.extend(mangas)
                    logger.info(f"Retrieved {len(mangas)} manga from page {page} (adult={adult})")
                    
                    if limit and len(manga_list) >= limit:
                        manga_list = manga_list[:limit]
                        return manga_list
                        
                    page += 1
                    
            logger.info(f"Total manga retrieved: {len(manga_list)}")
            return manga_list
            
        except Exception as e:
            logger.error(f"Error getting manga list: {e}")
            return []
            
    def _get_mangas_from_page(self, page: int, adult: bool = False) -> List[Dict[str, Any]]:
        """Get manga from a specific API page"""
        try:
            api_url = f"{self.api_url}/query?perPage=100&page={page}&adult={str(adult).lower()}"
            response = self._make_request(api_url)
            data = response.json()
            
            manga_list = []
            
            if 'data' in data and data['data']:
                for manga in data['data']:
                    try:
                        manga_list.append({
                            'id': json.dumps({'id': manga['id'], 'slug': manga['series_slug']}),
                            'title': manga['title'],
                            'source': self.label,
                            'url': urljoin(self.base_url, f"/series/{manga['series_slug']}"),
                            'type': 'webtoon',
                            'language': 'english',
                            'adult': adult,
                            'series_slug': manga['series_slug'],
                            'series_id': manga['id']
                        })
                    except Exception as e:
                        logger.error(f"Error parsing manga data: {e}")
                        continue
                        
            return manga_list
            
        except Exception as e:
            logger.error(f"Error getting manga from page {page}: {e}")
            return []
            
    def search_manga(self, query: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Search for manga on OmegaScans"""
        try:
            # Try API search first
            results = self._search_api(query, limit)
            if results:
                logger.info(f"Found {len(results)} manga matching '{query}' via API")
                return results

            # Fallback to local filtering if API search fails
            logger.warning(f"API search failed for '{query}', falling back to local filtering")
            all_manga = self.get_mangas(limit=200)  # Reduced from 500 for better performance

            query_lower = query.lower()
            results = []

            for manga in all_manga:
                if query_lower in manga['title'].lower():
                    results.append(manga)
                    if limit and len(results) >= limit:
                        break

            logger.info(f"Found {len(results)} manga matching '{query}' via local filtering")
            return results

        except Exception as e:
            logger.error(f"Error searching manga: {e}")
            return []

    def _search_api(self, query: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Search using OmegaScans API"""
        try:
            # Try the query endpoint with search parameter
            search_url = f"{self.api_url}/query/{query.replace(' ', '%20')}"
            response = self._make_request(search_url)
            data = response.json()

            results = []
            if 'data' in data and data['data']:
                for manga in data['data'][:limit] if limit else data['data']:
                    try:
                        results.append({
                            'id': json.dumps({'id': manga['id'], 'slug': manga['series_slug']}),
                            'title': manga['title'],
                            'source': self.label,
                            'url': urljoin(self.base_url, f"/series/{manga['series_slug']}"),
                            'type': 'webtoon',
                            'language': 'english',
                            'adult': manga.get('adult', False),
                            'series_slug': manga['series_slug'],
                            'series_id': manga['id']
                        })
                    except Exception as e:
                        logger.error(f"Error parsing search result: {e}")
                        continue

            return results

        except Exception as e:
            logger.warning(f"API search failed for '{query}': {e}")
            return []
            
    def get_chapters(self, manga: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get chapters for a specific manga"""
        try:
            chapters = []
            
            # Try V1 API first
            chapters = self._get_chapters_v1(manga)
            
            # If V1 fails, try V2 API
            if not chapters:
                chapters = self._get_chapters_v2(manga)
                
            logger.info(f"Found {len(chapters)} chapters for {manga['title']}")
            return chapters
            
        except Exception as e:
            logger.error(f"Error getting chapters: {e}")
            return []
            
    def _get_chapters_v1(self, manga: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get chapters using V1 API"""
        try:
            manga_data = json.loads(manga['id'])
            slug = manga_data['slug']
            
            api_url = f"{self.api_url}/series/{slug}"
            response = self._make_request(api_url)
            data = response.json()
            
            chapters = []
            
            if 'seasons' in data:
                for season in data['seasons']:
                    for chapter in season['chapters']:
                        season_prefix = f"S{season['index']} " if len(data['seasons']) > 1 else ""
                        title = f"{season_prefix}{chapter['chapter_name']} {chapter.get('chapter_title', '')}".strip()
                        
                        chapters.append({
                            'id': json.dumps({
                                'id': chapter['id'],
                                'slug': chapter['chapter_slug']
                            }),
                            'title': title,
                            'url': urljoin(self.base_url, f"/series/{slug}/{chapter['chapter_slug']}"),
                            'manga_id': manga['id'],
                            'chapter_slug': chapter['chapter_slug'],
                            'chapter_id': chapter['id']
                        })
                        
            return chapters
            
        except Exception as e:
            logger.error(f"Error with V1 chapters API: {e}")
            return []
            
    def _get_chapters_v2(self, manga: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get chapters using V2 API"""
        try:
            manga_data = json.loads(manga['id'])
            series_id = manga_data['id']
            manga_slug = manga_data['slug']
            
            api_url = f"{self.api_url}/chapter/query?series_id={series_id}&perPage=9999&page=1"
            response = self._make_request(api_url)
            data = response.json()
            
            chapters = []
            
            if 'data' in data:
                for chapter in data['data']:
                    title = f"{chapter['chapter_name']} {chapter.get('chapter_title', '')}".strip()
                    
                    # Store both manga and chapter slugs for pages endpoint
                    chapters.append({
                        'id': json.dumps({
                            'manga_slug': manga_slug,
                            'chapter_slug': chapter['chapter_slug'],
                            'chapter_id': chapter['id']
                        }),
                        'title': title,
                        'url': urljoin(self.base_url, f"/chapter/{chapter['chapter_slug']}"),
                        'manga_id': manga['id'],
                        'chapter_slug': chapter['chapter_slug'],
                        'chapter_id': chapter['id']
                    })
                    
            return chapters
            
        except Exception as e:
            logger.error(f"Error with V2 chapters API: {e}")
            return []
            
    def get_pages(self, chapter: Dict[str, Any]) -> List[str]:
        """Get page URLs for a chapter using slug-based endpoint (HakuNeko approach)"""
        try:
            chapter_data = json.loads(chapter['id'])
            manga_slug = chapter_data.get('manga_slug')
            chapter_slug = chapter_data.get('chapter_slug')
            
            if not manga_slug or not chapter_slug:
                logger.error(f"Missing manga_slug or chapter_slug in chapter data")
                return []
            
            # Use slug-based endpoint like HakuNeko: /chapter/{manga_slug}/{chapter_slug}
            api_url = f"{self.api_url}/chapter/{manga_slug}/{chapter_slug}"
            response = self._make_request(api_url)
            data = response.json()
            
            # Check for paywall
            if data.get('paywall'):
                logger.warning(f"Chapter {chapter['title']} is paywalled")
                raise Exception(f"Chapter is paywalled. Please login.")
            
            # Check if it's a novel (text-based content)
            if 'chapter' in data:
                chapter_info = data['chapter']
                if chapter_info.get('chapter_type', '').lower() == 'novel':
                    logger.warning(f"Chapter {chapter['title']} is a novel, not supported")
                    return []
            
            pages = []
            
            # Get images from data or chapter.chapter_data.images
            images = data.get('data') or (data.get('chapter', {}).get('chapter_data', {}).get('images', []))
            
            if not images:
                logger.error(f"No images found in chapter data")
                return []
            
            # Get storage type to determine URL format
            storage = data.get('chapter', {}).get('storage', 's3')
            
            for image in images:
                if isinstance(image, dict):
                    image_url = image.get('src') or image.get('url', '')
                else:
                    image_url = image
                
                if not image_url:
                    continue
                
                # Handle storage types like HakuNeko
                if storage == 's3':
                    # S3 URLs are already complete
                    pages.append(image_url)
                elif storage == 'local':
                    # Local storage needs API base URL
                    if not image_url.startswith('http'):
                        image_url = urljoin(self.api_url, image_url)
                    pages.append(image_url)
                else:
                    # Unknown storage, try as-is
                    pages.append(image_url)
                            
            logger.info(f"Found {len(pages)} pages for chapter {chapter['title']} (storage: {storage})")
            return pages
            
        except Exception as e:
            logger.error(f"Error getting pages: {e}")
            return []
    
    def get_manga_details(self, manga: Dict[str, Any]) -> Dict[str, Any]:
        """Get detailed information about a manga including cover image from OmegaScans API"""
        try:
            details = {
                'title': manga.get('title', ''),
                'url': manga.get('url', ''),
                'cover_url': None,
                'description': '',
                'status': '',
                'genres': [],
                'authors': []
            }
            
            # Try API first, but fall back to web scraping if it fails
            manga_id = manga.get('series_id') or manga.get('manga_id')
            
            # If manga_id is still None, try to extract from the JSON id field
            if not manga_id and manga.get('id'):
                try:
                    id_data = json.loads(manga['id']) if isinstance(manga['id'], str) else manga['id']
                    manga_id = id_data.get('id') if isinstance(id_data, dict) else None
                except:
                    pass
            
            # Try API approach first
            if manga_id:
                try:
                    api_url = f"{self.api_url}/query/{manga_id}"
                    response = self._make_request(api_url)
                    data = response.json()
                    
                    if 'data' in data and data['data']:
                        manga_data = data['data']
                        
                        # Extract cover image from API
                        if 'thumbnail' in manga_data:
                            cover_url = manga_data['thumbnail']
                            if cover_url.startswith('//'):
                                details['cover_url'] = 'https:' + cover_url
                            elif cover_url.startswith('/'):
                                details['cover_url'] = urljoin(self.base_url, cover_url)
                            else:
                                details['cover_url'] = cover_url
                        
                        # Extract other details from API
                        details['description'] = manga_data.get('description', '')
                        details['status'] = manga_data.get('status', '')
                        
                        if 'genres' in manga_data:
                            details['genres'] = manga_data['genres']
                        
                        if 'authors' in manga_data:
                            details['authors'] = manga_data['authors']
                        
                        return details
                
                except Exception as api_error:
                    logger.warning(f"API failed for {manga.get('title')}, trying web scraping: {api_error}")
            
            # Fallback to web scraping
            manga_url = manga.get('url')
            if manga_url:
                try:
                    response = self._make_request(manga_url)
                    soup = BeautifulSoup(response.content, 'html.parser')
                    
                    # Use optimized extraction method
                    cover_url = self._extract_cover_omegascans(soup, self.base_url)
                    if cover_url:
                        details['cover_url'] = cover_url
                    
                    # Try to extract description
                    desc_selectors = [
                        '.series-description',
                        '.manga-description', 
                        '.summary',
                        'p[class*="description"]'
                    ]
                    
                    for selector in desc_selectors:
                        desc_elem = soup.select_one(selector)
                        if desc_elem:
                            details['description'] = desc_elem.get_text(strip=True)
                            break
                
                except Exception as scrape_error:
                    logger.warning(f"Web scraping also failed for {manga.get('title')}: {scrape_error}")
            
            return details
            
        except Exception as e:
            logger.error(f"Error getting manga details for {manga.get('title')}: {e}")
            return {'title': manga.get('title', ''), 'cover_url': None}
    
    def _extract_cover_omegascans(self, soup, base_url):
        """Extract cover image from OmegaScans with optimized selectors"""
        # Strategy 1: Use optimized OmegaScans-specific selectors
        cover_selectors = [
            'img[src*="media.omegascans.org/series"][src*="/cover"]',  # Series covers
            'img.series-poster',
            'img.manga-poster',
            'img.series-cover',
            'img[src*="media.omegascans.org"]',                        # All OmegaScans media
            'img[src*="_next/image"][src*="media.omegascans.org"]',   # Next.js optimized images
            '.series-info img:first-of-type',
            '[data-cover] img',
            'img[alt*="series" i]',
        ]
        
        for selector in cover_selectors:
            try:
                cover_img = soup.select_one(selector)
                if cover_img:
                    cover_src = cover_img.get('src') or cover_img.get('data-src')
                    if cover_src and len(cover_src) > 15:  # Must be reasonable URL
                        # Extract actual URL from Next.js optimization if needed
                        if '/_next/image' in cover_src and 'url=' in cover_src:
                            try:
                                import urllib.parse
                                parsed = urllib.parse.urlparse(cover_src)
                                params = urllib.parse.parse_qs(parsed.query)
                                if 'url' in params:
                                    cover_src = params['url'][0]
                            except:
                                pass
                        
                        cover_url = self._normalize_url(cover_src, base_url)
                        if cover_url:
                            logger.debug(f"Found OmegaScans cover via '{selector}': {cover_url[:80]}")
                            return cover_url
            except Exception as e:
                logger.debug(f"Selector '{selector}' failed: {e}")
                continue
        
        # Strategy 2: Scan all images for cover-like patterns
        all_imgs = soup.find_all('img', limit=100)
        for img in all_imgs:
            src = img.get('src') or img.get('data-src')
            if not src or len(src) < 15:
                continue
            
            src_lower = src.lower()
            
            # Check for cover/poster patterns in OmegaScans URLs
            if 'media.omegascans.org' in src_lower:
                # Extract from Next.js if needed
                if '/_next/image' in src_lower and 'url=' in src_lower:
                    try:
                        import urllib.parse
                        parsed = urllib.parse.urlparse(src)
                        params = urllib.parse.parse_qs(parsed.query)
                        if 'url' in params:
                            src = params['url'][0]
                    except:
                        pass
                
                # Skip obvious non-covers
                if any(skip in src_lower for skip in ['avatar', 'logo', 'icon', 'badge']):
                    continue
                
                cover_url = self._normalize_url(src, base_url)
                if cover_url:
                    logger.debug(f"Found OmegaScans cover via media scan: {cover_url[:80]}")
                    return cover_url
        
        logger.debug(f"No cover found via OmegaScans extraction")
        return None
    
    def _normalize_url(self, url, base_url):
        """Normalize and validate cover URL"""
        if not url or len(url) < 10:
            return None
        
        try:
            if url.startswith('//'):
                return 'https:' + url
            elif url.startswith('/'):
                return urljoin(base_url, url)
            elif url.startswith('http'):
                return url
            else:
                return None
        except:
            return None


class mangaConnectorManager:
    """Manager class to handle multiple manga connectors"""
    
    def __init__(self):
        self.connectors = {
            'mangafox': mangaFoxConnector(),
            'omegascans': OmegaScansConnector(),
            'hentaifox': HentaiFoxConnector()
        }
        
    def get_connector(self, name: str) -> Optional[mangaConnector]:
        """Get a specific connector by name"""
        return self.connectors.get(name)
        
    def get_all_connectors(self) -> Dict[str, mangaConnector]:
        """Get all available connectors"""
        return self.connectors
        
    def search_all(self, query: str, limit_per_source: int = 10) -> Dict[str, List[Dict[str, Any]]]:
        """Search across all connectors"""
        results = {}
        
        for name, connector in self.connectors.items():
            try:
                connector_results = connector.search_manga(query, limit=limit_per_source)
                results[name] = connector_results
                logger.info(f"{name} returned {len(connector_results)} results for '{query}'")
            except Exception as e:
                logger.error(f"Error searching {name}: {e}")
                results[name] = []
                
        return results


class HentaiFoxConnector:
    """Connector for HentaiFox manga source"""
    
    def __init__(self):
        self.base_url = "https://hentaifox.com"
        self.image_base_url = "https://i.hentaifox.com"
        self.name = "HentaiFox"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def search(self, query, page=1, sort="latest"):
        """
        Search for manga on HentaiFox
        
        Args:
            query (str): Search keyword
            page (int): Page number (default: 1)
            sort (str): Sort method - "latest" or "popular" (default: "latest")
            
        Returns:
            list: List of manga dictionaries with id, title, cover, category
        """
        try:
            url = f"{self.base_url}/search/?q={query}&sort={sort}&page={page}"
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            results = []
            
            # Find all gallery titles
            titles = soup.select('h2.g_title')
            
            for title_elem in titles:
                try:
                    # Extract title text
                    title_text = title_elem.get_text(strip=True)
                    
                    # Extract ID from href
                    link = title_elem.find('a')
                    if not link or not link.get('href'):
                        continue
                    
                    href_parts = link.get('href').split('/')
                    gallery_id = href_parts[2] if len(href_parts) > 2 else None
                    if not gallery_id or not gallery_id.isdigit():
                        continue
                    
                    # Find corresponding cover image
                    # Images are in the grandparent container (div.thumb)
                    parent = title_elem.find_parent('div')  # caption div
                    grandparent = parent.find_parent('div') if parent else None  # thumb div
                    if grandparent:
                        img = grandparent.find('img', {'data-src': True})
                        cover_url = img.get('data-src', '') if img else ''
                    else:
                        cover_url = ''
                    
                    # Find category
                    category_elem = parent.find('h3', class_='g_cat') if parent else None
                    category = ''
                    if category_elem:
                        cat_link = category_elem.find('a')
                        if cat_link and cat_link.get('href'):
                            cat_parts = cat_link.get('href').split('/')
                            category = cat_parts[2] if len(cat_parts) > 2 else ''
                    
                    results.append({
                        'id': gallery_id,
                        'title': title_text,
                        'cover': cover_url,
                        'category': category,
                        'link': f"{self.base_url}/gallery/{gallery_id}/",
                        'source': 'hentaifox'
                    })
                    
                except Exception as e:
                    logger.error(f"Error parsing result: {e}")
                    continue
            
            return results
            
        except Exception as e:
            logger.error(f"Search error for HentaiFox: {e}")
            return []
    
    def get_manga_details(self, manga_id):
        # Defensive: if manga_id is a dict, extract the string
        if isinstance(manga_id, dict):
            manga_id = manga_id.get('id') or manga_id.get('manga_id') or str(manga_id)
        """
        Get detailed information about a manga
        
        Args:
            manga_id (str): The manga ID
            
        Returns:
            dict: manga details including title, id, tags, total_pages, image_base_url
        """
        try:
            url = f"{self.base_url}/gallery/{manga_id}/"
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract title
            title_elem = soup.select_one('div.info h1')
            title = title_elem.get_text(strip=True) if title_elem else f"Gallery {manga_id}"
            
            # Extract tags
            tag_elems = soup.select('a.tag_btn')
            tags = [tag.get_text(strip=True).replace(' ', '').rstrip('0123456789') 
                   for tag in tag_elems]
            
            # Extract page count
            pages_elem = soup.select_one('span.i_text.pages')
            total_pages = 0
            if pages_elem:
                pages_text = pages_elem.get_text(strip=True)
                page_match = re.search(r'(\d+)', pages_text)
                if page_match:
                    total_pages = int(page_match.group(1))
            
            # Extract image base URL and extension from the actual gallery page
            # Look for img tags with data-src attribute
            img_elems = soup.select('img[data-src]')
            image_base_url = None
            extension = '.jpg'  # default
            
            if img_elems:
                # Get first image URL and extract pattern
                first_img_src = img_elems[0].get('data-src', '')
                if first_img_src:
                    # Extract base URL and extension
                    # Pattern: https://i.hentaifox.com/149500/1.webp
                    parts = first_img_src.rsplit('/', 1)
                    if len(parts) == 2:
                        image_base_url = parts[0]  # https://i.hentaifox.com/149500
                        # Extract extension from filename
                        filename = parts[1]
                        if '.' in filename:
                            extension = '.' + filename.split('.')[-1]
            
            # Fallback to constructed URL if extraction failed
            if not image_base_url:
                image_base_url = f"{self.image_base_url}/{manga_id}"
                extension = self._predict_extension(manga_id, image_base_url)
            else:
                # Even if we got base URL, the extension from thumbnail may be wrong
                # Verify the actual full-size image extension
                extension = self._predict_extension(manga_id, image_base_url)
            
            return {
                'id': manga_id,
                'title': title,
                'tags': tags,
                'total_pages': total_pages,
                'extension': extension,
                'image_base_url': image_base_url,
                'source': 'hentaifox'
            }
            
        except Exception as e:
            logger.error(f"Error getting manga details: {e}")
            return None
    
    def _predict_extension(self, manga_id, image_base_url):
        """
        Predict image extension by checking if .jpg or .webp is available
        
        Args:
            manga_id (str): Gallery ID
            image_base_url (str): Base URL for images
            
        Returns:
            str: '.jpg' or '.webp'
        """
        try:
            # Test if .jpg works
            test_url = f"{image_base_url}/1.jpg"
            response = requests.head(test_url, headers=self.headers, timeout=10, allow_redirects=True)
            
            if response.status_code == 200:
                return '.jpg'
            else:
                return '.webp'
        except:
            # Default to .webp if check fails
            return '.webp'
    
    def get_chapters(self, manga_id):
        # Defensive: if manga_id is a dict, extract the string
        if isinstance(manga_id, dict):
            manga_id = manga_id.get('id') or manga_id.get('manga_id') or str(manga_id)
        """
        Get chapter list for a manga
        HentaiFox has single-chapter galleries, so return one chapter
        
        Args:
            manga_id (str): The manga ID
            
        Returns:
            list: List with single chapter dictionary
        """
        details = self.get_manga_details(manga_id)
        if not details:
            return []
        
        return [{
            'id': manga_id,
            'title': 'Complete Gallery',
            'manga_id': manga_id,
            'chapter_number': '1',
            'total_pages': details.get('total_pages', 0),
            'extension': details.get('extension', '.jpg'),
            'image_base_url': details.get('image_base_url')
        }]
    
    def get_pages(self, chapter):
        """
        Get list of page URLs for a chapter
        
        Args:
            chapter (dict): Chapter dictionary with id, manga_id, total_pages, extension, image_base_url
            
        Returns:
            list: List of page URL strings
        """
        try:
            manga_id = chapter.get('manga_id') or chapter.get('id')
            # Defensive: if manga_id is a dict, extract the string
            if isinstance(manga_id, dict):
                manga_id = manga_id.get('manga_id') or manga_id.get('id')
            total_pages = chapter.get('total_pages', 0)
            extension = chapter.get('extension', '.jpg')
            image_base_url = chapter.get('image_base_url')
            
            if not manga_id or total_pages == 0 or not image_base_url:
                # Try to fetch details if not provided
                details = self.get_manga_details(manga_id)
                if details:
                    total_pages = details['total_pages']
                    extension = details['extension']
                    image_base_url = details.get('image_base_url')
            
            # Use the extracted image base URL from the gallery page
            if not image_base_url:
                image_base_url = f"{self.image_base_url}/{manga_id}"
            
            # Generate page URLs
            pages = []
            for page_num in range(1, total_pages + 1):
                page_url = f"{image_base_url}/{page_num}{extension}"
                pages.append(page_url)
            
            return pages
            
        except Exception as e:
            logger.error(f"Error getting pages: {e}")
            return []
        
    def get_popular_manga(self, limit_per_source: int = 20) -> Dict[str, List[Dict[str, Any]]]:
        """Get popular manga from all connectors"""
        results = {}
        
        for name, connector in self.connectors.items():
            try:
                popular_manga = connector.get_mangas(limit=limit_per_source)
                results[name] = popular_manga
                logger.info(f"{name} returned {len(popular_manga)} popular manga")
            except Exception as e:
                logger.error(f"Error getting popular manga from {name}: {e}")
                results[name] = []
                
        return results


# Example usage
if __name__ == "__main__":
    # Test the connectors
    manager = mangaConnectorManager()
    
    print("Testing OmegaScans connector...")
    omega = manager.get_connector('omegascans')
    if omega:
        manga_list = omega.get_mangas(limit=5)
        print(f"Found {len(manga_list)} manga from OmegaScans")
        for manga in manga_list[:3]:
            print(f"- {manga['title']}")
            
        # Test chapters
        if manga_list:
            chapters = omega.get_chapters(manga_list[0])
            print(f"Found {len(chapters)} chapters for {manga_list[0]['title']}")
    
    print("\nTesting mangaFox connector...")
    mangafox = manager.get_connector('mangafox')
    if mangafox:
        manga_list = mangafox.get_mangas(limit=5)
        print(f"Found {len(manga_list)} manga from mangaFox")
        for manga in manga_list[:3]:
            print(f"- {manga['title']}")
