#!/usr/bin/env python3
"""
YouTube Cookie Setup for Nukedown
This script helps you set up cookies to avoid YouTube CAPTCHA issues
"""

import os
import sys

def create_cookie_instructions():
    """Create instructions for setting up YouTube cookies"""
    print("YouTube Cookie Setup Instructions")
    print("=" * 40)
    print()
    print("To avoid 'Sign in to confirm you're not a bot' errors, you need to:")
    print("1. Use cookies from a real browser session")
    print("2. Export cookies from Chrome/Firefox")
    print("3. Save them as 'youtube_cookies.txt' in the config folder")
    print()
    print("METHOD 1: Using Chrome (Recommended)")
    print("-" * 40)
    print("1. Install the 'Get cookies.txt' extension from Chrome Web Store")
    print("   https://chrome.google.com/webstore/detail/get-cookiestxt/bgaddhkoddajcdgocldbbfleckgcbcid")
    print("2. Visit youtube.com and sign in to your Google account")
    print("3. Click the extension icon and select 'Export as cookies.txt'")
    print("4. Save the file as 'youtube_cookies.txt' in the nukedown-docker/config/ folder")
    print()
    print("METHOD 2: Using Firefox")
    print("-" * 40)
    print("1. Install the 'Export Cookies' extension")
    print("   https://addons.mozilla.org/en-US/firefox/addon/export-cookies-txt/")
    print("2. Visit youtube.com and sign in")
    print("3. Click the extension icon to export cookies")
    print("4. Save as Netscape format to 'nukedown-docker/config/youtube_cookies.txt'")
    print()
    print("METHOD 3: Manual Cookie Creation")
    print("-" * 40)
    print("If you have specific cookies, create a cookies.txt file with this format:")
    print("# Netscape HTTP Cookie File")
    print("# https://curl.haxx.se/rfc/cookie_spec.html")
    print("# This is a generated file!  Do not edit.")
    print()
    print(".youtube.com	TRUE	/	FALSE	1735689600	SID	your_sid_value_here")
    print(".youtube.com	TRUE	/	FALSE	1735689600	HSID	your_hsid_value_here")
    print(".youtube.com	TRUE	/	FALSE	1735689600	SSID	your_ssid_value_here")
    print(".youtube.com	TRUE	/	FALSE	1735689600	SAPISID	your_sapisid_value_here")
    print(".youtube.com	TRUE	/	FALSE	1735689600	SAPISIDHASH	your_sapisidhash_value_here")
    print()
    print("LOCATION: Save the file at:")
    config_path = os.path.join(os.path.dirname(__file__), 'config', 'youtube_cookies.txt')
    print(f"  {config_path}")
    print()
    print("TESTING:")
    print("-" * 40)
    print("After setting up cookies, restart nukedown and try downloading a video.")
    print("If you still get CAPTCHA errors, try refreshing your browser cookies.")
    print()
    print("TROUBLESHOOTING:")
    print("-" * 40)
    print("• Make sure you're signed into YouTube in the browser you export from")
    print("• Try using a different browser (Chrome works best)")
    print("• Cookies expire, so you may need to re-export them periodically")
    print("• If issues persist, try using a VPN or different IP address")

def check_cookie_file():
    """Check if cookie file exists"""
    cookie_path = os.path.join(os.path.dirname(__file__), 'config', 'youtube_cookies.txt')
    if os.path.exists(cookie_path):
        print(f"✅ Cookie file found at: {cookie_path}")
        # Check file size
        size = os.path.getsize(cookie_path)
        if size > 100:
            print(f"✅ Cookie file appears to have content ({size} bytes)")
        else:
            print(f"⚠️  Cookie file is very small ({size} bytes) - may not be valid")
    else:
        print(f"❌ Cookie file not found at: {cookie_path}")
        print("   Run this script to see setup instructions.")

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--check':
        check_cookie_file()
    else:
        create_cookie_instructions()
        print()
        check_cookie_file()