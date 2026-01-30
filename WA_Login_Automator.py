import uiautomator2 as u2
import time
import re
import xml.etree.ElementTree as ET
import subprocess
import os
import shutil
import sys
import tempfile
import signal

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False
    print("⚠️ Playwright not available. Install: pip install playwright && python -m playwright install")

# Global variable to keep browser context open after code is extracted
BROWSER_CONTEXT = None
BROWSER_PLAYWRIGHT = None
BROWSER_PAGE = None

def safe_sleep(duration):
    """Sleep that propagates keyboard interrupts so user can exit"""
    time.sleep(duration)

# ... (Previous imports) ...

# =================================================
# BROWSER AUTOMATION FUNCTIONS (PLAYWRIGHT)
# =================================================
def open_with_playwright(url, session_dir, phone_number=None):
    """Playwright ka use karke Chromium launch karega specifically.
       Follows: Link with phone number -> Enter Number -> Get Code.
    """
    global BROWSER_CONTEXT, BROWSER_PLAYWRIGHT, BROWSER_PAGE
    
    if not PLAYWRIGHT_AVAILABLE:
        print("❌ Playwright not installed. Falling back to system browser.")
        if sys.platform.startswith('win'):
            subprocess.Popen(['cmd', '/c', 'start', url], shell=True)
        return None

    print(f"🌐 Launching Chromium via Playwright...")
    try:
        # Initialize Playwright but DON'T use context manager - keep browser open
        p = sync_playwright().start()
        BROWSER_PLAYWRIGHT = p
        
        # Use the actual persistent session directory
        try:
            os.makedirs(session_dir, exist_ok=True)
            user_data_dir = session_dir
            print(f"📂 Using persistent profile: {user_data_dir}")
        except Exception:
            user_data_dir = tempfile.mkdtemp(prefix="whatsapp_temp_")
            print(f"⚠️ Could not use session_dir, using temp: {user_data_dir}")
        
        # Enhanced launch args to prevent crashes on Windows
        # Disable GPU, disable dev-shm, disable sandbox, disable various problematic features
        launch_args = [
            "--no-sandbox",
            "--disable-gpu",                         # Disable GPU acceleration (common crash cause)
            "--disable-dev-shm-usage",              # Use regular memory instead of /dev/shm
            "--disable-web-resources",              # Disable preloading web resources
            "--disable-extensions",                 # No extensions
            "--disable-plugins",                    # No plugins
            "--no-first-run",                       # Skip first-run setup
            "--disable-default-apps",               # No default apps
            "--disable-popup-blocking",             # Allow popups (needed for WhatsApp)
            "--disable-translate",                  # Disable translation
            "--disable-sync",                       # Disable sync
            "--disable-background-networking",      # No background networking
            "--disable-component-update",           # No component updates
            "--disable-breakpad",                   # No crash reporter
            "--disable-client-side-phishing-detection", # Disable phishing detection
        ]
        
        print(f"🔧 Browser args: {launch_args}")
        
        # Try to launch with persistent context
        try:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                args=launch_args,
                timeout=60000,  # 60 second timeout
                slow_mo=100     # Slow down operations for stability
            )
            print("✅ Browser launched successfully (persistent context)")
        except Exception as persistent_err:
            print(f"⚠️ Persistent context failed: {persistent_err}")
            print("🔄 Trying regular browser launch instead...")
            
            # Fallback: launch regular browser without persistent context
            browser = p.chromium.launch(
                headless=False,
                args=launch_args,
                timeout=60000
            )
            print("✅ Browser launched successfully (regular context)")
        
        # Store browser context globally so we can keep it open after code extraction
        BROWSER_CONTEXT = browser
        
        # Create or get page
        if hasattr(browser, 'pages') and browser.pages:
            page = browser.pages[0]
        else:
            # Regular browser launch - need to create context and page
            context = browser.new_context()
            page = context.new_page()
            BROWSER_CONTEXT = context  # Update context reference
        
        BROWSER_PAGE = page # Store page globally for login check
        
        # Try to navigate with retry
        navigation_success = False
        for attempt in range(3):
            try:
                safe_sleep(2)  # Give browser time to initialize
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                print("✅ WhatsApp Web opened in Chromium")
                navigation_success = True
                break
            except KeyboardInterrupt:
                print("⚠️ Navigation interrupted but continuing...")
                pass
            except Exception as e:
                if attempt < 2:
                    print(f"⚠️ Navigation error (attempt {attempt+1}/3): {e}")
                    safe_sleep(2)
                else:
                    print(f"⚠️ Navigation failed after 3 attempts: {e}")
                    pass
        
        # DEBUG: Save page HTML and screenshot for inspection
        try:
            time.sleep(3)  # Wait for page to fully load
            
            # CHECK IF ALREADY LOGGED IN (Persistence check)
            # If logged in, we will see chat list or profile picture
            print("🔎 Checking login status...")
            is_logged_in = False
            try:
                # Look for common elements available only when logged in
                if page.locator("#pane-side").count() > 0 or \
                   page.get_by_test_id("chat-list").count() > 0 or \
                   page.locator("div[role='textbox']").count() > 0 or \
                   page.get_by_title("Profile").count() > 0:
                    is_logged_in = True
                    print("✅ ALREADY LOGGED IN! Skipping phone linking.")
                    return "LOGGED_IN"
            except Exception:
                pass

            if not is_logged_in:
                print("ℹ️ Not logged in yet. Proceeding with linking...")

            html_content = page.content()
            with open("whatsapp_web_debug.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            print("📄 Saved page HTML: whatsapp_web_debug.html")
        except Exception as e:
            print(f"⚠️ Could not save HTML/Check login: {e}")
        
        try:
            page.screenshot(path="whatsapp_web_debug.png")
            print("📸 Saved debug screenshot: whatsapp_web_debug.png")
        except Exception:
            pass

        # AUTOMATE "Link with phone number" FLOW
        if phone_number:
            print(f"📞 Automating 'Link with phone number' for {phone_number}...")
            
            # 1. Click "Link with phone number" — try multiple selector strategies
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
                time.sleep(2)  # Extra wait for JS to render
                
                found = False
                # Try specific text matches first
                text_candidates = [
                    "Log in with phone number",
                    "Link with phone number",
                    "Log in with phone",
                    "Link with phone",
                    "Login with phone number",
                    "Sign in with phone",
                    "Use phone number"
                ]

                for txt in text_candidates:
                    try:
                        locator = page.get_by_text(txt, exact=False)
                        if locator.count() > 0:
                            el = locator.first
                            # Ensure element is visible and scroll into view
                            el.scroll_into_view_if_needed()
                            time.sleep(0.3)
                            
                            # Check if visible before clicking
                            if el.is_visible():
                                el.click()
                                print(f"✅ Clicked by text: {txt}")
                                time.sleep(2)  # Wait for page change after click
                                found = True
                                break
                            else:
                                print(f"  ℹ️ Element found but not visible: {txt}")
                    except Exception as e:
                        pass

                if not found:
                    print("⚠️ 'Link with phone number' button not found or not clickable.")
            except Exception as e:
                print(f"⚠️ Error finding Link button: {e}")

            # 2. Enter Phone Number
            try:
                print("⏳ Waiting for input fields to appear...")
                # Try multiple input selectors
                input_selectors = [
                    "input[type='text']",
                    "input[type='tel']",
                    "input",
                    "textarea",
                    "[contenteditable='true']",
                    "[role='textbox']"
                ]
                
                inputs_found = []
                for sel in input_selectors:
                    try:
                        loc = page.locator(sel)
                        if loc.count() > 0:
                            inputs_found.extend(loc.all())
                            print(f"Found {loc.count()} elements with {sel}")
                            break  # Use first match
                    except Exception:
                        pass
                
                # Try to find the phone input (usually 2nd input if country picker is 1st)
                if inputs_found:
                    phone_input = inputs_found[-1]  # Use the last input field found
                    phone_input.scroll_into_view_if_needed()
                    time.sleep(0.5)
                    
                    # Clear any existing text and type the number
                    phone_input.click()
                    time.sleep(0.3)
                    phone_input.clear()
                    phone_input.type(phone_number, delay=50)
                    print(f"✅ Entered phone number: {phone_number}")
                    time.sleep(1)
                    
                    # 3. Click NEXT
                    try:
                        next_btn = page.get_by_text("Next", exact=True)
                        if next_btn.count() > 0:
                            next_el = next_btn.first
                            next_el.scroll_into_view_if_needed()
                            time.sleep(0.3)
                            if next_el.is_visible():
                                next_el.click()
                                print("✅ Clicked 'Next'")
                                time.sleep(3)  # Wait for code to generate
                        else:
                            # Try finding button by role
                            next_role = page.get_by_role("button", name="Next")
                            if next_role.count() > 0:
                                next_role.first.click()
                                print("✅ Clicked 'Next' (by role)")
                                time.sleep(3)
                    except Exception as ne:
                        print(f"⚠️ Error clicking Next: {ne}")
                else:
                    print("⚠️ No input fields found for phone number entry")
            except Exception as e:
                print(f"⚠️ Error entering phone number: {e}")

        # Code extracting logic - wait for REAL linking code (8 alphanumeric with dash: LXW1-41BJ)
        # Don't reload page - extract code from current page after phone number is entered
        time.sleep(2)  # Give page time to generate linking code
        
        print("🔎 Looking for linking code (format XXXX-XXXX, waiting 45s)...")
        start_time = time.time()
        
        # Regex patterns to try - handle various formats including characters separated by newlines
        regex_std = re.compile(r"\b([A-Z0-9]{4})[\s\-\u2013\u2014\n]+([A-Z0-9]{4})\b")
        
        # Pattern to handle code where each character is on its own line: 2\n4\n6\nJ\n-\nX\n5\n4\n4\n1
        regex_newline = re.compile(r"([A-Z0-9])\n([A-Z0-9])\n([A-Z0-9])\n([A-Z0-9])\n[\-\n]*([A-Z0-9])\n([A-Z0-9])\n([A-Z0-9])\n([A-Z0-9])")
        
        attempt = 0
        code = None
        debug_saved = False

        while time.time() - start_time < 45:
            attempt += 1
            try:
                # Get text from multiple sources to be safe
                body_text = page.inner_text("body")
                
                # SAVE DEBUG TEXT ON FIRST ATTEMPT
                if attempt == 1 and not debug_saved:
                    print(f"\n🔍 DEBUG: Extracted page text (first 1000 chars):\n{body_text[:1000]}\n")
                    with open("whatsapp_page_text_debug.txt", "w", encoding="utf-8") as f:
                        f.write(body_text)
                    print("📝 Full page text saved to: whatsapp_page_text_debug.txt\n")
                    debug_saved = True
                
                # PATTERN 1: Try standard regex first (XXXX-XXXX format)
                matches = regex_std.finditer(body_text)
                found_code = None
                for m in matches:
                    p1, p2 = m.groups()
                    
                    # FILTER: Skip common English words
                    ignored_words = {"LINK", "WITH", "SCAN", "CODE", "CAST", "STAY", "OPEN", "WHATS", "APPS", "TYPE", "THIS", "YOUR", "MAIN", "MENU", "BACK", "DIGIT", "MODAL", "ENTER", "IPHONE"}
                    if p1 in ignored_words or p2 in ignored_words:
                        continue
                    
                    found_code = f"{p1}-{p2}"
                    break
                
                if found_code:
                    code = found_code
                    print(f"✅ Found linking code (standard): {code}")
                    page.screenshot(path="whatsapp_web_code.png")
                    break
                
                # PATTERN 2: Try detecting code where each char is on its own line (2\n4\n6\nJ\n-\nX\n5\n4\n4\n1)
                newline_matches = regex_newline.finditer(body_text)
                for m in newline_matches:
                    groups = m.groups()
                    # Extract the 8 characters (groups 0-3 and 4-7)
                    code_str = groups[0] + groups[1] + groups[2] + groups[3] + "-" + groups[4] + groups[5] + groups[6] + groups[7]
                    # Basic validation - should have numbers/letters
                    if len(code_str) == 9 and code_str[4] == "-":
                        code = code_str
                        print(f"✅ Found linking code (newline-separated): {code}")
                        page.screenshot(path="whatsapp_web_code.png")
                        break
                
                if code:
                    break

                # FALLBACK: Try to find code in specific WhatsApp container (attempt 5+)
                if attempt >= 5 and attempt % 5 == 0:
                    try:
                        # Try common selectors where WhatsApp displays code
                        code_selectors = [
                            "[data-testid*='code']",
                            "[class*='code']",
                            "[class*='linking']",
                            "span[class*='bold']",
                        ]
                        for selector in code_selectors:
                            try:
                                elements = page.query_selector_all(selector)
                                for el in elements:
                                    el_text = page.evaluate("el => el.textContent", el).strip()
                                    # Check if text looks like a code
                                    if re.search(r"[A-Z0-9]{4}[-\s][A-Z0-9]{4}", el_text):
                                        code = re.sub(r"[\s\n]+", "", el_text)
                                        if len(code) >= 7:  # At least XXXXYYYY
                                            print(f"✅ Found linking code in {selector}: {code}")
                                            page.screenshot(path="whatsapp_web_code.png")
                                            break
                            except:
                                pass
                        if code:
                            break
                    except:
                        pass

                # Every 5 seconds, show progress
                if attempt % 5 == 0:
                    print(f"  ⏳ Waiting... {45 - (time.time() - start_time):.0f}s remaining")
                    
            except Exception as e:
                if attempt % 10 == 0:
                    print(f"  ⚠️ Error during extraction: {e}")
            
            time.sleep(1)  # Check every 1 second
        
        if not code:
            print("⚠️ Linking code not found after 45s. Saving debug screenshot...")
            page.screenshot(path="whatsapp_web_code_timeout.png")
            print("💡 Check WhatsApp Web screen and enter code manually if available.")
           
            # DO NOT CLOSE - keep browser open for user inspection (ONLY if code NOT found)
            print("\n🔒 Browser window remains OPEN.")
            print("⏳ Keeping browser open indefinitely (Ctrl+C to exit)...")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n👋 Saving debug files and closing browser...")
                try:
                    page.screenshot(path="whatsapp_web_final_debug.png")
                    with open("whatsapp_web_final_debug.html", "w", encoding="utf-8") as f:
                        f.write(page.content())
                    print("✅ Debug files saved (whatsapp_web_final_debug.png, whatsapp_web_final_debug.html)")
                except:
                    pass
                try:
                    browser.close()
                    BROWSER_CONTEXT.close()
                except:
                    pass  # Ignore close errors
        else:
            # Code was found! Keep browser open during phone entry and 5-min wait
            print(f"\n✅ Linking code detected: {code}")
            print("📲 Returning to phone entry...")
            print("🌐 Browser will stay open during linking process...")
        
        return code  # Return code - browser will stay open for linking

    except Exception as e:
        print(f"⚠️ Playwright Error: {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        # Browser stays open after function returns - will be closed in main function after 5-min wait
        pass

# Renamed/Replaces get_code_with_pyppeteer
def get_code_from_browser(session_dir, phone_number):
    return open_with_playwright("https://web.whatsapp.com", session_dir, phone_number)


def close_browser_context():
    """Close the browser context that was kept open after code extraction."""
    global BROWSER_CONTEXT, BROWSER_PLAYWRIGHT
    try:
        if BROWSER_CONTEXT:
            BROWSER_CONTEXT.close()
            print("✅ Browser closed")
        if BROWSER_PLAYWRIGHT:
            BROWSER_PLAYWRIGHT.stop()
    except:
        pass
    BROWSER_CONTEXT = None
    BROWSER_PLAYWRIGHT = None


def load_phone_list(path_candidates=("phones.txt", "phones.csv")):
    """Try to load phone numbers from common files in workspace. Returns list of cleaned numbers."""
    for p in path_candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    lines = [l.strip() for l in f.readlines()]
                nums = []
                for l in lines:
                    if not l:
                        continue
                    # extract digits and plus
                    m = re.search(r"[+\d][\d\s\-()]+", l)
                    if m:
                        cleaned = re.sub(r"[^+0-9]", "", m.group())
                        nums.append(cleaned)
                if nums:
                    return nums
            except Exception:
                pass
    return []
chrome_profile_arg = None
chrome_profile = None
machine_number = None

# Command line argument check (similar to Node.js: process.argv[2])
if len(sys.argv) > 1:
    chrome_profile_arg = sys.argv[1]
    print(f"✅ Command line argument: {chrome_profile_arg}")
else:
    # Force user to provide code like C1_M1 or CR5_R1
    while True:
        entered = input("🔑 Enter profile code (e.g., C1_M1 or CR5_R1): ").strip()
        if entered:
            chrome_profile_arg = entered
            break
        print("⚠️ Code required. Try again.")

# Regex pattern (same as Node.js)
if "R" in chrome_profile_arg:
    # Pattern: CR5_R1 type
    regex = re.compile(r'C([^_]+)_([^\s]+)')
else:
    # Pattern: C138_M7 type
    regex = re.compile(r'C([^_]+)_M(\d+)')

regex_match = regex.match(chrome_profile_arg)

if regex_match:
    chrome_profile = regex_match.group(1)  # Extract number after C
    machine_number = regex_match.group(2)  # Extract number/part after _
    print(f"✅ Chrome Profile: C{chrome_profile}")
    print(f"✅ Machine Number: {machine_number}")
else:
    raise SystemExit("⚠️ Invalid code. Expected: C<number>_M<number> or CR<number>_R<number>")

# Get Phone Number for Web Linking
PHONE_NUMBER = None
# If a file path is provided as argv[3], try loading numbers from it
phone_list = []
if len(sys.argv) > 3:
    arg3 = sys.argv[3]
    # If arg3 is a path to a file, try to load list
    if os.path.exists(arg3):
        try:
            with open(arg3, 'r', encoding='utf-8') as f:
                phone_list = [l.strip() for l in f if l.strip()]
            print(f"✅ Loaded {len(phone_list)} numbers from {arg3}")
        except Exception:
            phone_list = load_phone_list()
    else:
        # direct phone argument (number or index)
        if re.match(r"^\d+$", arg3) and len(arg3) > 6:
            PHONE_NUMBER = arg3
            print(f"✅ Phone Number from arg: {PHONE_NUMBER}")
        else:
            # maybe user passed an index to choose from default list
            phone_list = load_phone_list()
            if phone_list and arg3.isdigit():
                idx = int(arg3) - 1
                if 0 <= idx < len(phone_list):
                    PHONE_NUMBER = phone_list[idx]
                    print(f"✅ Selected phone #{arg3}: {PHONE_NUMBER}")

# If no argv number, try to load phones.txt or phones.csv in workspace
if not PHONE_NUMBER and not phone_list:
    phone_list = load_phone_list()

# If we have a list, prompt user to choose one (unless auto provided)
if phone_list and not PHONE_NUMBER:
    print("📋 Phone numbers available:")
    for i, num in enumerate(phone_list, start=1):
        print(f"{i}. {num}")
    try:
        choice = input("👉 Select phone number index (or press ENTER to use first): ").strip()
        if choice and choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(phone_list):
                PHONE_NUMBER = phone_list[idx]
            else:
                PHONE_NUMBER = phone_list[0]
        else:
            PHONE_NUMBER = phone_list[0]
    except Exception:
        PHONE_NUMBER = phone_list[0]

if not PHONE_NUMBER:
    try:
        PHONE_NUMBER = input("📞 Enter Phone Number (with country code, e.g. 919876543210): ").strip()
    except Exception:
        PHONE_NUMBER = None

if not PHONE_NUMBER:
    print("⚠️ No phone number provided. Web linking might fail if manual input is needed.")

# Folder structure (Node.js pattern: ../${chromeProfileArg}/.wwebjs_auth)
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

# Username-agnostic Desktop path: <home>/Desktop/<chrome_profile_arg>
home_dir = os.path.expanduser("~")
base_dir = os.path.join(home_dir, "Desktop", chrome_profile_arg)

profile_path = os.path.join(base_dir, ".wwebjs_auth")
cache_path = os.path.join(base_dir, ".wwebjs_cache")

os.makedirs(profile_path, exist_ok=True)
os.makedirs(cache_path, exist_ok=True)

print(f"📂 Session Auth Dir: {profile_path}")
print(f"📂 Session Cache Dir: {cache_path}")

try:
    from PIL import ImageGrab
    import pytesseract
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False
    print("⚠️ OCR not available (optional). Install: pip install pillow pytesseract")

# =================================================
# BROWSER AUTOMATION FUNCTIONS
# =================================================
def open_in_chromium(url: str) -> bool:
    """Chrome/Chromium browser me URL open karta hai. Prioritizes Chromium as requested."""
    candidates = []
    
    # 1. Environment variables
    env_path = os.environ.get('CHROMIUM_PATH') or os.environ.get('CHROME_PATH')
    if env_path:
        candidates.append(env_path)

    # 2. Look for local/pyppeteer Chromium installs (often in %LOCALAPPDATA% on Windows)
    if sys.platform.startswith('win'):
        local_app_data = os.environ.get('LOCALAPPDATA')
        if local_app_data:
            pyppeteer_base = os.path.join(local_app_data, 'pyppeteer', 'local-chromium')
            if os.path.exists(pyppeteer_base):
                # Find the latest build
                for item in sorted(os.listdir(pyppeteer_base), reverse=True):
                    candidate = os.path.join(pyppeteer_base, item, 'chrome-win', 'chrome.exe')
                    if os.path.exists(candidate):
                        candidates.append(candidate)
                        break

    # 3. Known binary names (Chromium first, then Chrome as fallback)
    search_names = [
        'chromium', 'chromium-browser',       # Linux/Mac/Win with Chromium in PATH
        'chrome', 'chrome.exe', 'google-chrome', # Regular Chrome
        'msedge.exe'                          # Edge (last resort)
    ]

    for name in search_names:
        try:
            path = shutil.which(name)
        except Exception:
            path = None
        if path and path not in candidates:
            candidates.append(path)

    for p in candidates:
        try:
            subprocess.Popen([p, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"🌐 Opening {url} in browser: {p}")
            return True
        except Exception:
            pass

    # Fallback to system default
    try:
        if sys.platform.startswith('win'):
            subprocess.Popen(['cmd', '/c', 'start', url], shell=True)
        else:
            subprocess.Popen(['xdg-open', url])
        print(f"🌐 Opening {url} with system default browser")
        return True
    except Exception:
        print(f"⚠️ Could not open browser. Please manually open: {url}")
        return False


async def get_code_with_pyppeteer_async(timeout=60, headless=False, session_dir=None):
    """Pyppeteer (Puppeteer) se WhatsApp Web open karke code extract karta hai."""
    try:
        # Use provided session_dir or default
        if session_dir is None:
            session_dir = os.path.join(os.path.dirname(__file__), "whatsapp_sessions", "default")
            os.makedirs(session_dir, exist_ok=True)
        
        print(f"📁 Session folder: {session_dir}")
        
        print(f"🌐 Launching Chrome via Pyppeteer (headless={headless})...")
        browser = await launch({
            'headless': headless,
            'userDataDir': session_dir,  # Session save hoga yahan
            'args': [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-web-security',
                '--start-maximized',
                f'--user-data-dir={session_dir}'
            ],
            'defaultViewport': None
        })
        
        page = await browser.newPage()
        await page.goto('https://web.whatsapp.com', {'timeout': 30000})
        print("✅ WhatsApp Web opened successfully")
        
        digit_re = re.compile(r"\b(\d{4,8})\b")
        end = time.time() + timeout
        
        print(f"🔎 Looking for numeric code on page (timeout {timeout}s)...")
        while time.time() < end:
            try:
                # Page ka text content read karo
                text = await page.evaluate('() => document.body.innerText')
                if text:
                    m = digit_re.search(text)
                    if m:
                        code = m.group(1)
                        print(f"✅ Code found on WhatsApp Web: {code}")
                        # Screenshot save karo
                        try:
                            await page.screenshot({'path': 'whatsapp_web_code.png'})
                            print("📸 Screenshot saved: whatsapp_web_code.png")
                        except Exception:
                            pass
                        await asyncio.sleep(2)  # User ko dikhne ke liye
                        await browser.close()
                        return code
            except Exception as e:
                pass
            
            await asyncio.sleep(2)
        
        # Timeout ke baad screenshot save karo
        print("⚠️ Code not found automatically. Browser will stay open...")
        try:
            await page.screenshot({'path': 'whatsapp_web_final.png'})
        except Exception:
            pass
        
        print("🌐 Browser is open. Please check WhatsApp Web for the code.")
        # Browser ko open rakhne ke liye ek minute wait karo
        await asyncio.sleep(60)
        await browser.close()
        return None
        
    except Exception as e:
        print(f"⚠️ Pyppeteer error: {e}")
        return None


def get_code_with_pyppeteer(timeout=60, headless=False, session_dir=None):
    """Sync wrapper for async Pyppeteer function."""
    if not PYPPETEER_AVAILABLE:
        print("⚠️ Pyppeteer not available. Opening browser manually...")
        open_in_chromium('https://web.whatsapp.com')
        return None
    
    try:
        # Windows event loop policy fix
        if sys.platform.startswith('win'):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        code = loop.run_until_complete(get_code_with_pyppeteer_async(timeout, headless, session_dir))
        return code
    except Exception as e:
        print(f"⚠️ Pyppeteer execution error: {e}")
        print("📱 Falling back to manual browser opening...")
        open_in_chromium('https://web.whatsapp.com')
        return None


def enter_code_on_phone(device, code):
    """Phone me code enter karta hai."""
    if not code:
        return False
    
    # Remove hyphen for phone entry
    clean_code = code.replace("-", "").strip()
    print(f"📲 Entering code on phone: {code} (sending as {clean_code})")
    
    try:
        # Strategy 1: Find focused element or any EditText and type
        # Sometimes fields are split (8 EditTexts), sometimes one hidden EditText
        
        # Click on the center of the screen or look for an input field to focus
        # Often the first field is auto-focused, but let's try to find an EditText
        edit_texts = device(className="android.widget.EditText")
        if edit_texts.exists(timeout=2):
            edit_texts[0].click() # Click first one to focus
            time.sleep(0.5)
            # Try plain text input
            device.shell(f"input text '{clean_code}'")
            print("✅ Code entered via ADB input text")
            return True
        
        # Strategy 2: If no EditText found (custom views), assume focus is ready or click center
        # Wait a bit
        time.sleep(1)
        device.shell(f"input text '{clean_code}'")
        print("✅ Code entered via ADB input text (fallback)")
        return True
            
    except Exception as e:
        print(f"⚠️ Failed to enter code: {e}")
        return False


# =================================================
# CONNECT
# =================================================
try:
    d = u2.connect()
    d.screen_on()
    d.unlock()
except Exception as e:
    print(f"❌ Failed to connect to device: {e}")
    raise SystemExit("Device connection failed")

# =================================================
# ANDROID USERS (FOR DUAL APPS)
# =================================================
def get_android_users():
    out = subprocess.check_output(
        ["adb", "shell", "pm", "list", "users"],
        universal_newlines=True
    )
    users = []
    for line in out.splitlines():
        m = re.search(r'UserInfo\{(\d+):', line)
        if m:
            users.append(int(m.group(1)))
    return users

# =================================================
# BUILD WHATSAPP INSTANCES (PACKAGE + USER)
# =================================================
instances = []
users = get_android_users()
packages = ["com.whatsapp", "com.whatsapp.w4b"]

for pkg in packages:
    for user in users:
        try:
            subprocess.check_output(
                ["adb", "shell", "pm", "path", "--user", str(user), pkg],
                stderr=subprocess.DEVNULL
            )
            instances.append((pkg, user))
        except subprocess.CalledProcessError:
            pass

if not instances:
    raise SystemExit("❌ No WhatsApp found on device")

print("\n📱 WhatsApp instances found:\n")
for i, (pkg, user) in enumerate(instances, start=1):
    if pkg == "com.whatsapp" and user == 0:
        label = "WhatsApp (Normal)"
    elif pkg == "com.whatsapp" and user != 0:
        label = f"WhatsApp Dual (user {user})"
    elif pkg == "com.whatsapp.w4b":
        label = "WhatsApp Business"
    else:
        label = f"{pkg} (user {user})"
    print(f"{i}. {label}")

# Allow auto-selection via command line arg (argv[2])
if len(sys.argv) > 2 and sys.argv[2].isdigit():
    choice = int(sys.argv[2])
    print(f"\n👉 Auto-selected WhatsApp index from arg: {choice}")
else:
    try:
        choice_input = input("\n👉 Select WhatsApp to open: ").strip()
        # Clean inputs like "1. WhatsApp" -> "1"
        if choice_input and choice_input[0].isdigit():
            choice = int(re.match(r'\d+', choice_input).group())
        else:
            choice = int(choice_input)
    except Exception:
        choice = 1
        print("⚠️ Input error, defaulting to 1")

if 1 <= choice <= len(instances):
    PACKAGE, USER_ID = instances[choice - 1]
else:
    PACKAGE, USER_ID = instances[0]
    print("⚠️ Invalid choice, defaulting to 1")

print(f"\n✅ Selected: {PACKAGE} (user {USER_ID})\n")

# =================================================
# BUTTON DETECTOR
# =================================================
def detect_buttons(device):
    xml = device.dump_hierarchy()
    root = ET.fromstring(xml)
    buttons = []

    def center(bounds):
        nums = list(map(int, re.findall(r"\d+", bounds)))
        if len(nums) == 4:
            x1, y1, x2, y2 = nums
            return (x1 + x2)//2, (y1 + y2)//2
        return None

    def walk(node):
        a = node.attrib
        pkg = a.get("package", "")
        cls = a.get("class", "") or ""
        text_raw = a.get("text", "") or ""
        desc_raw = a.get("content-desc", "") or a.get("content_desc", "") or ""
        res_raw = a.get("resource-id", "") or ""
        text = text_raw.strip().lower()
        desc = desc_raw.strip().lower()
        res = res_raw.strip().lower()
        bounds = a.get("bounds", "")
        clickable = a.get("clickable", "false")

        # consider element if clickable or looks like a button or has text/desc/res
        if bounds and (clickable == "true" or "button" in cls.lower() or text or desc or res):
            c = center(bounds)
            if c:
                buttons.append({
                    "pkg": pkg,
                    "class": cls,
                    "text": text,
                    "text_raw": text_raw,
                    "desc": desc,
                    "desc_raw": desc_raw,
                    "res": res,
                    "res_raw": res_raw,
                    "x": c[0],
                    "y": c[1]
                })

        for ch in node:
            walk(ch)

    walk(root)
    return buttons

# =================================================
# CLEAR RECENT APPS (MIUI REAL SWIPE)
# =================================================
def clear_recent_apps(device, max_swipes=10):
    print("🧹 Clearing recent apps")
    device.shell("input keyevent KEYCODE_APP_SWITCH")
    time.sleep(2)

    for _ in range(max_swipes):
        buttons = detect_buttons(device)
        cards = [b for b in buttons if "unlocked" in b["desc"]]

        if not cards:
            print("✅ Recent apps cleared")
            break

        b = cards[0]
        device.shell(
            f"input swipe {b['x']} {b['y']} {b['x'] - 700} {b['y']} 200"
        )
        time.sleep(0.5)

    device.shell("input keyevent KEYCODE_HOME")
    time.sleep(1)

# =================================================
# HANDLE APP CHOOSER (POSITION-BASED – DUAL SAFE)
# =================================================
def handle_app_chooser(device, pkg, user_id, timeout=6):
    print("🔎 Checking for app chooser…")
    end = time.time() + timeout

    while time.time() < end:
        buttons = detect_buttons(device)

        chooser = [
            b for b in buttons
            if b["pkg"] in ("android", "com.android.systemui") and b["text"]
        ]

        if len(chooser) >= 2:
            chooser.sort(key=lambda x: x["y"])

            if pkg == "com.whatsapp" and user_id != 0:
                target = chooser[1]   # DUAL
                print("✅ Selecting DUAL WhatsApp (2nd option)")
            else:
                target = chooser[0]   # NORMAL / BUSINESS
                print("✅ Selecting NORMAL WhatsApp (1st option)")

            try:
                device.click(target['x'], target['y'])
            except Exception:
                device.shell(f"input tap {target['x']} {target['y']}")
            time.sleep(1)
            return True

        time.sleep(0.4)

    print("ℹ️ No chooser dialog detected")
    return False

# =================================================
# WAIT FOR WHATSAPP (DUAL-SAFE)
# =================================================
def wait_for_whatsapp(device, pkg, timeout=20):
    print("⏳ Waiting for WhatsApp to be ready...")
    end = time.time() + timeout

    while time.time() < end:
        # 1️⃣ Normal foreground check
        cur = device.app_current()
        if cur and cur.get("package") == pkg:
            print("✅ WhatsApp foreground (app_current)")
            return True

        # 2️⃣ UI-based fallback (dual-safe)
        buttons = detect_buttons(device)
        wa_ui = [
            b for b in buttons
            if b["pkg"] == pkg and (
                "menuitem_overflow" in b["res"]
                or "new chat" in b["text"]
                or "chats" in b["text"]
            )
        ]

        if wa_ui:
            print("✅ WhatsApp UI detected (dual-safe)")
            return True

        safe_sleep(0.6)

    return False

# =================================================
# SMART CLICK (FINAL PRIORITY LOGIC)
# =================================================
def smart_click(device, keywords, timeout=8):
    end = time.time() + timeout

    while time.time() < end:
        buttons = detect_buttons(device)
        wa = [b for b in buttons if b.get("pkg") == PACKAGE or PACKAGE in (b.get("pkg") or "")]

        # 1) resource-id matches (preferred)
        for b in wa:
            if b.get('res') and any(k in b['res'] for k in keywords):
                try:
                    rid = b.get('res_raw')
                    if rid and device(resourceId=rid).exists(timeout=0.8):
                        device(resourceId=rid).click()
                        return True
                except Exception:
                    pass
                # fallback: click center
                try:
                    device.click(b['x'], b['y'])
                    return True
                except Exception:
                    pass

        # 2) text matches
        for b in wa:
            try:
                txt = b.get('text_raw')
                if txt and any(k in b.get('text','') for k in keywords):
                    if device(text=txt).exists(timeout=0.8):
                        device(text=txt).click()
                        return True
                    else:
                        device.click(b['x'], b['y'])
                        return True
            except Exception:
                pass

        # 3) description matches
        for b in wa:
            try:
                dsc = b.get('desc_raw')
                if dsc and any(k in b.get('desc','') for k in keywords):
                    if device(description=dsc).exists(timeout=0.8):
                        device(description=dsc).click()
                        return True
                    else:
                        device.click(b['x'], b['y'])
                        return True
            except Exception:
                pass

        time.sleep(0.4)

    return False

# =================================================
# RESET + OPEN WHATSAPP
# =================================================
clear_recent_apps(d)

print("🛑 Force-stopping WhatsApp")
d.shell(f"am force-stop --user {USER_ID} {PACKAGE}")
safe_sleep(1)

print("📱 Opening WhatsApp…")
d.shell(f"am start --user {USER_ID} -n {PACKAGE}/com.whatsapp.Main")

handle_app_chooser(d, PACKAGE, USER_ID)

# 🔥 CRITICAL: WAIT UNTIL UI IS READY
if not wait_for_whatsapp(d, PACKAGE):
    print("🔁 Retry opening WhatsApp once…")
    d.shell(f"am start --user {USER_ID} -n {PACKAGE}/com.whatsapp.Main")
    safe_sleep(2)
    if not wait_for_whatsapp(d, PACKAGE):
        raise SystemExit("❌ WhatsApp did not become ready")

safe_sleep(2)

# =================================================
# WHATSAPP AUTOMATION FLOW
# =================================================

# 1. Start Browser FIRST to check if already logged in
print("\n" + "="*50)
print("🌐 CHECKING BROWSER SESSION...")
print("="*50 + "\n")

# Session directory info
if chrome_profile_arg:
    print(f"📂 Profile: {chrome_profile_arg}")
    print(f"📂 Chrome Profile: C{chrome_profile}")
    print(f"📂 Machine: {machine_number}")
    print(f"💾 Session path: {profile_path}")
else:
    print(f"📂 Using default session (No command line argument)")
    print(f"💾 Session path: {profile_path}")

print("🚀 Launching Playwright Chromium...")
code = get_code_from_browser(profile_path, PHONE_NUMBER)

# IF ALREADY LOGGED IN: STOP HERE
if code == "LOGGED_IN":
    print("\n🎉 SESSION RESTORED: You are already logged in to WhatsApp Web!")
    print("✅ No need to link device again.")
    print("🌐 Browser will stay open. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n👋 Closing browser...")
        close_browser_context()
        sys.exit(0)

# IF NOT LOGGED IN: PROCEED WITH PHONE AUTOMATION
print("\n" + "="*50)
print("📱 STARTING PHONE AUTOMATION (Linking Required)...")
print("="*50 + "\n")

print("⋮ Opening menu")
if not smart_click(d, ["menuitem_overflow", "more"]):
    raise SystemExit("Menu not found")

safe_sleep(1)

print("🔗 Opening Linked devices")
if not smart_click(d, ["linked"]):
    raise SystemExit("Linked devices not found")

safe_sleep(2)

print("🟢 Clicking Link a device")
if not smart_click(d, ["link_device"]):
    raise SystemExit("Link a device not found")

safe_sleep(2)

print("📞 Clicking Link with phone number")
smart_click(d, ["phone"])
safe_sleep(3)

# Agar code mil gaya to phone me enter karo
if code:
    print(f"\n📱 Code detected: {code}")
    print("📲 Attempting to enter code on phone...")
    time.sleep(2)  # Phone UI ready hone do
    
    success = enter_code_on_phone(d, code)
    if success:
        print("✅ Code entered successfully!")
        print("⏳ Waiting for login to complete (max 5 minutes)...")
        print("💡 NOTE: You can press Ctrl+C to close the window immediately if logged in.")
        print("🌐 Keep browser open - Checking for login status...")
        
        # Wait 5 minutes but check for login/interrupt
        try:
            for remaining in range(300, 0, -1):  # 5 minutes = 300 seconds
                if remaining % 30 == 0 or remaining <= 10:
                    print(f"  ⏱️ {remaining}s remaining...")
                
                # Check if logged in (look for chat list)
                if BROWSER_PAGE and not BROWSER_PAGE.is_closed():
                    try:
                        # Common selectors that appear after login
                        if BROWSER_PAGE.locator("#pane-side").count() > 0 or \
                           BROWSER_PAGE.get_by_test_id("chat-list").count() > 0 or \
                           BROWSER_PAGE.locator("div[role='textbox']").count() > 0:
                            print("\n✅ LOGIN DETECTED! WhatsApp Web is active.")
                            print("🎉 You are successfully logged in.")
                            break
                    except Exception:
                        pass
                        
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n👋 User interrupted (Ctrl+C). Closing browser and exiting...")
            close_browser_context()
            sys.exit(0)
        except Exception as e:
            print(f"\n⚠️ Browser error during wait: {e}")
        
        print("\n✅ Process complete - closing browser...")
        close_browser_context()
        print("\n🎉 AUTOMATION COMPLETE – Device should be linked!")
        print(f"\n💾 Login session saved in: {profile_path}")
        if cache_path:
            print(f"💾 Cache saved in: {cache_path}")
        print("✅ Next time browser automatically logged in rahega!")
    else:
        print("⚠️ Could not enter code automatically.")
        print(f"💡 Please manually enter this code on phone: {code}")
        print("⏳ Waiting 30s for manual entry...")
        time.sleep(30)
        close_browser_context()
else:
    # Manual code entry option
    print("\n⚠️ Code not detected automatically.")
    print("💭 Browser is open - please check WhatsApp Web and enter the OTP manually if needed.")
    try:
        manual_code = input("\n👉 Enter the code from WhatsApp Web (or press ENTER to skip): ").strip()
        if manual_code:
            enter_code_on_phone(d, manual_code)
            print("⏳ Waiting 5 minutes for login to complete...")
            try:
                for remaining in range(300, 0, -1):
                    if remaining % 30 == 0 or remaining <= 10:
                        print(f"  ⏱️ {remaining}s remaining...")
                    time.sleep(1)
            except Exception as e:
                print(f"\n⚠️ Error during wait (continuing anyway): {e}")
        close_browser_context()
    except KeyboardInterrupt:
        print("\n⏸️ Interrupted by user")
        close_browser_context()
    except Exception:
        close_browser_context()
        pass

print("\n" + "="*50)
print("🎉 FLOW COMPLETE – NORMAL / DUAL / BUSINESS ALL WORKING")
print("📸 Check for screenshots: whatsapp_web_code.png, whatsapp_web_final.png")
if PLAYWRIGHT_AVAILABLE:
    print(f"\n💾 Session saved in:")
    print(f"   Auth: {profile_path}")
    if cache_path:
        print(f"   Cache: {cache_path}")
    if chrome_profile_arg:
        print(f"\n💡 Next run: python Tester.py {chrome_profile_arg}")
    print("✅ Next run me automatically login rahega (scan nahi karna padega)")
print("="*50)

# Keep alive
input("\n✅ Press ENTER to exit...")
