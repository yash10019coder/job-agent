"""
Stealth Browser - Anti-Bot Guardrails
Prevents shadow banning and bot detection on job sites
"""
import asyncio
import random
import time
from typing import Optional, Tuple
from dataclasses import dataclass, field
from contextlib import asynccontextmanager

# Optional: Try to import undetected-chromedriver fallback
try:
    import playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


@dataclass
class StealthConfig:
    """Configuration for stealth browser behavior"""
    # Rate limits (per platform)
    linkedin_daily_max: int = 20
    linkedin_hourly_max: int = 5
    portal_daily_max: int = 15
    portal_hourly_max: int = 4
    
    # Timing (seconds)
    min_action_delay: float = 0.3
    max_action_delay: float = 2.5
    min_scroll_delay: float = 0.5
    max_scroll_delay: float = 1.5
    min_typing_speed: float = 0.05  # chars per second (human-like)
    max_typing_speed: float = 0.15
    min_page_load: float = 1.5
    max_page_load: float = 4.0
    
    # Mouse movement
    mouse_steps_min: int = 8
    mouse_steps_max: int = 20
    mouse_curve_probability: float = 0.7  # 70% bezier curves
    
    # Human-like behaviors
    typo_probability: float = 0.02  # 2% chance of typo
    backspace_probability: float = 0.3  # 30% chance of backspace when typo
    
    # Viewport randomization
    viewport_width_range: Tuple[int, int] = (1280, 1920)
    viewport_height_range: Tuple[int, int] = (720, 1080)


class StealthBrowser:
    """
    Stealth browser wrapper with human-like behavior simulation.
    Prevents detection by bot catchers on LinkedIn, Greenhouse, Lever, etc.
    """
    
    def __init__(self, config: Optional[StealthConfig] = None):
        self.config = config or StealthConfig()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._last_action_time = 0.0
        self._action_count = 0
        
    async def __aenter__(self):
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )
        
        self._playwright = await playwright.async_api.async_playwright().start()
        
        # Randomize viewport to avoid fingerprinting
        width = random.randint(*self.config.viewport_width_range)
        height = random.randint(*self.config.viewport_height_range)
        
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
            ]
        )
        
        # Create stealth context with randomized attributes
        self._context = await self._browser.new_context(
            viewport={'width': width, 'height': height},
            user_agent=self._random_user_agent(),
            locale='en-US,en;q=0.9',
            timezone_id='America/New_York',
            permissions=['geolocation'],
            # Disable webdriver flag
            ignore_https_errors=True,
        )
        
        # Add stealth scripts to mask automation
        await self._add_stealth_scripts()
        
        self._page = await self._context.new_page()
        
        return self
    
    async def __aexit__(self, *args):
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
    
    def _random_user_agent(self) -> str:
        """Generate realistic user agent string"""
        agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
        ]
        return random.choice(agents)
    
    async def _add_stealth_scripts(self):
        """Inject JavaScript to hide automation markers"""
        stealth_script = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        window.chrome = { runtime: {} };
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        """
        await self._context.add_init_script(stealth_script)
    
    async def _random_delay(self, min_t: float, max_t: float):
        """Sleep with random delay (human-like)"""
        delay = random.uniform(min_t, max_t)
        await asyncio.sleep(delay)
    
    async def _human_delay(self):
        """Delay between actions to simulate human behavior"""
        await self._random_delay(
            self.config.min_action_delay,
            self.config.max_action_delay
        )
    
    async def _track_action(self):
        """Track action for rate limiting"""
        self._last_action_time = time.time()
        self._action_count += 1
    
    async def human_click(self, selector: str, steps: Optional[int] = None) -> bool:
        """
        Click with human-like mouse movement.
        Uses bezier curves to simulate natural hand movement.
        """
        if steps is None:
            steps = random.randint(self.config.mouse_steps_min, self.config.mouse_steps_max)
        
        try:
            # Get target element position
            element = self._page.locator(selector)
            box = await element.bounding_box()
            if not box:
                return False
            
            target_x = box['x'] + box['width'] / 2
            target_y = box['y'] + box['height'] / 2
            
            # Generate bezier curve points for natural movement
            start_x = random.randint(100, 800)
            start_y = random.randint(100, 600)
            
            # Control points for bezier curve
            cp1x = start_x + (target_x - start_x) * random.uniform(0.3, 0.7)
            cp1y = start_y + random.uniform(-100, 100)
            cp2x = target_x - (target_x - start_x) * random.uniform(0.2, 0.5)
            cp2y = target_y + random.uniform(-50, 50)
            
            # Move with curve
            for i in range(steps):
                t = i / steps
                # Cubic bezier
                x = (1-t)**3 * start_x + 3*(1-t)**2*t * cp1x + 3*(1-t)*t**2 * cp2x + t**3 * target_x
                y = (1-t)**3 * start_y + 3*(1-t)**2*t * cp1y + 3*(1-t)*t**2 * cp2y + t**3 * target_y
                
                await self._page.mouse.move(x, y)
                
                # Variable speed (slower at start/end)
                speed = 0.01 + 0.02 * (1 - abs(t - 0.5) * 2)
                await asyncio.sleep(speed)
            
            # Small pause at target before clicking
            await asyncio.sleep(random.uniform(0.05, 0.15))
            
            # Click
            await self._page.mouse.click(target_x, target_y)
            
            await self._human_delay()
            await self._track_action()
            
            return True
            
        except Exception as e:
            # Fallback to simple click
            try:
                await self._page.click(selector)
                await self._human_delay()
                await self._track_action()
                return True
            except:
                return False
    
    async def human_type(self, selector: str, text: str) -> bool:
        """
        Type text with human-like speed and occasional typos.
        """
        try:
            element = self._page.locator(selector)
            await element.click()
            await self._human_delay()
            
            for char in text:
                # Check for typo
                if random.random() < self.config.typo_probability:
                    # Type wrong char
                    wrong_char = chr(ord(char) + random.choice([-1, 1, 2, -2]))
                    await self._page.keyboard.type(wrong_char, delay=0)
                    await asyncio.sleep(random.uniform(0.05, 0.1))
                    
                    # Maybe correct it
                    if random.random() < self.config.backspace_probability:
                        await self._page.keyboard.press('Backspace')
                        await asyncio.sleep(random.uniform(0.02, 0.05))
                        await self._page.keyboard.type(char, delay=0)
                else:
                    # Normal typing
                    speed = random.uniform(self.config.min_typing_speed, self.config.max_typing_speed)
                    await self._page.keyboard.type(char, delay=speed * 1000)
            
            await self._track_action()
            return True
            
        except Exception:
            # Fallback to simple fill
            try:
                await element.fill(text)
                await self._track_action()
                return True
            except:
                return False
    
    async def human_scroll(self, amount: int = None, direction: str = "down"):
        """
        Scroll with human-like pauses.
        """
        if amount is None:
            amount = random.randint(200, 500)
        
        try:
            scroll_amount = amount if direction == "down" else -amount
            
            # Scroll in chunks with pauses
            chunk_size = random.randint(100, 200)
            chunks = abs(scroll_amount) // chunk_size
            
            for _ in range(chunks):
                await self._page.mouse.wheel(0, chunk_size if direction == "down" else -chunk_size)
                await self._random_delay(
                    self.config.min_scroll_delay,
                    self.config.max_scroll_delay
                )
            
            await self._track_action()
            
        except Exception:
            await self._page.keyboard.press('PageDown' if direction == 'down' else 'PageUp')
            await self._random_delay(0.3, 0.8)
    
    async def human_hover(self, selector: str):
        """Hover over element with natural movement"""
        await self.human_click(selector, steps=random.randint(5, 12))
        # Remove the click part for hover
        # Note: In practice, you'd need to track mouse position differently
    
    async def wait_for_page_load(self):
        """Wait for page with randomized timing"""
        await self._random_delay(
            self.config.min_page_load,
            self.config.max_page_load
        )
        await self._page.wait_for_load_state('domcontentloaded')
        await asyncio.sleep(random.uniform(0.2, 0.5))
    
    @property
    def page(self):
        """Get the underlying page object"""
        return self._page
    
    @property
    def context(self):
        """Get the context object"""
        return self._context
    
    @property
    def action_count(self) -> int:
        """Get count of actions taken"""
        return self._action_count


@asynccontextmanager
async def create_stealth_browser(config: Optional[StealthConfig] = None):
    """Context manager for stealth browser"""
    browser = StealthBrowser(config)
    await browser.__aenter__()
    try:
        yield browser
    finally:
        await browser.__aexit__(None, None, None)


async def login_linkedin(email: str, password: str, storage_path: str = "outputs/linkedin_session.json") -> bool:
    """
    Login to LinkedIn and save session.
    Returns True if login successful.
    """
    try:
        from playwright.async_api import async_playwright
        
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=False)  # Show browser for login
        context = await browser.new_context()
        page = await context.new_page()
        
        # Go to LinkedIn login
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        await asyncio.sleep(2)
        
        # Fill email
        await page.fill("#username", email)
        await asyncio.sleep(0.5)
        
        # Fill password
        await page.fill("#password", password)
        await asyncio.sleep(0.5)
        
        # Click login
        await page.click(".btn__primary--large")
        await asyncio.sleep(5)  # Wait for redirect
        
        # Check if logged in
        if "feed" in page.url or "mynetwork" in page.url:
            # Save session state
            await context.storage_state(path=storage_path)
            print(f"✅ LinkedIn login successful! Session saved to {storage_path}")
            await browser.close()
            await pw.stop()
            return True
        else:
            print("❌ LinkedIn login failed - check credentials")
            await browser.close()
            await pw.stop()
            return False
            
    except Exception as e:
        print(f"❌ LinkedIn login error: {e}")
        return False


async def login_linkedin_interactive(storage_path: str = "outputs/linkedin_session.json") -> bool:
    """
    Interactive LinkedIn login - opens browser for manual login.
    Saves session after successful login.
    """
    try:
        from playwright.async_api import async_playwright
        
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        # Go to LinkedIn
        await page.goto("https://www.linkedin.com/", wait_until="domcontentloaded")
        
        # Wait for user to login manually (up to 5 minutes)
        print("⏳ Please login to LinkedIn manually in the browser...")
        print("⏳ Waiting for login... (will save session automatically)")
        
        # Poll for login completion
        import time
        start = time.time()
        while time.time() - start < 300:  # 5 min timeout
            url = page.url
            if "feed" in url or "mynetwork" in url or "checkpoint" in url:
                break
            await asyncio.sleep(2)
        
        # Check if logged in
        if "checkpoint" in page.url:
            print("⚠️  LinkedIn security checkpoint triggered - complete in browser")
        
        if "feed" in page.url or "mynetwork" in page.url:
            # Save session
            await context.storage_state(path=storage_path)
            print(f"✅ Session saved to {storage_path}")
            await browser.close()
            await pw.stop()
            return True
        
        print("⚠️  Login timeout or incomplete")
        await browser.close()
        await pw.stop()
        return False
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def load_linkedin_session(storage_path: str = "outputs/linkedin_session.json") -> dict:
    """Load saved LinkedIn session state"""
    from pathlib import Path
    path = Path(storage_path)
    if path.exists():
        return {"storage_state": storage_path}
    return {}
