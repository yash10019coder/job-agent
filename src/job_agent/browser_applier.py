"""
Browser-Based Auto-Apply with Stealth Guardrails
Handles LinkedIn Easy Apply and other form-based applications
"""
import asyncio
import re
import os
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from job_agent.stealth import StealthBrowser, StealthConfig
from job_agent.rate_limiter import RateLimiter, get_rate_limiter, Platform


@dataclass
class BrowserApplyConfig:
    """Configuration for browser-based applications"""
    stealth: bool = True  # Use stealth mode (human-like behavior)
    timeout: int = 45000  # Increased timeout for slow pages
    retry_count: int = 3
    resume_upload_xpath: str = "//input[@type='file']"
    session_path: str = "outputs/linkedin_session.json"
    

@dataclass
class ApplyResult:
    """Result of an application attempt"""
    success: bool
    platform: str
    url: str
    message: str
    form_filled: int = 0
    fields_skipped: List[str] = None
    guardrail_triggered: bool = False
    
    def __post_init__(self):
        if self.fields_skipped is None:
            self.fields_skipped = []


class SmartBrowserApplyAgent:
    """
    Browser-based auto-apply with anti-bot guardrails.
    Uses stealth browser to avoid shadow banning.
    """
    
    def __init__(
        self,
        config: Optional[BrowserApplyConfig] = None,
        rate_limiter: Optional[RateLimiter] = None
    ):
        self.config = config or BrowserApplyConfig()
        self.rate_limiter = rate_limiter or get_rate_limiter()
        self._stealth: Optional[StealthBrowser] = None
        self._platform = None
    
    async def __aenter__(self):
        if self.config.stealth:
            stealth_config = StealthConfig()
            self._stealth = StealthBrowser(stealth_config)
            await self._stealth.__aenter__()
            
            # Load LinkedIn session if available
            session_path = self.config.session_path
            if os.path.exists(session_path):
                # Load saved session for authentication
                await self._stealth.context.new_page()
        return self
    
    async def __aexit__(self, *args):
        if self._stealth:
            await self._stealth.__aexit__(*args)
    
    def _detect_platform(self, url: str) -> str:
        """Detect platform from URL"""
        url_lower = url.lower()
        if 'linkedin.com' in url_lower:
            return 'linkedin'
        elif 'greenhouse.io' in url_lower:
            return 'greenhouse'
        elif 'lever.co' in url_lower:
            return 'lever'
        elif 'workday' in url_lower:
            return 'workday'
        elif 'ashbyhq' in url_lower:
            return 'ashby'
        return 'unknown'
    
    async def apply_linkedin(self, job_url: str, resume_path: str) -> ApplyResult:
        """Apply to LinkedIn Easy Apply with stealth mode and saved session"""
        self._platform = 'linkedin'
        
        # Check rate limits first
        can_apply, reason = self.rate_limiter.can_apply(job_url)
        if not can_apply:
            return ApplyResult(
                success=False,
                platform='linkedin',
                url=job_url,
                message=f"Rate limited: {reason}",
                guardrail_triggered=True
            )
        
        try:
            import playwright
            pw = await playwright.async_api.async_playwright().start()
            
            # Launch browser with saved session
            browser = await pw.chromium.launch(headless=False)
            
            # Load saved session
            session_path = self.config.session_path
            context = None
            if os.path.exists(session_path):
                context = await browser.new_context(storage_state=session_path)
            else:
                context = await browser.new_context()
            
            page = await context.new_page()
            
            # Navigate to job page
            await page.goto(job_url, timeout=self.config.timeout)
            await asyncio.sleep(2)  # Let dynamic content load
            
            # Check if we need to login
            if "login" in page.url.lower():
                return ApplyResult(
                    success=False,
                    platform='linkedin',
                    url=job_url,
                    message="Not logged in. Run login first: python -m job_agent login-linkedin"
                )
            
            # Scroll to load job description
            await page.evaluate("window.scrollBy(0, 300)")
            await asyncio.sleep(1)
            
            # Look for Easy Apply button
            easy_apply_selectors = [
                "button:has-text('Easy Apply')",
                "[data-control-name='Easy Apply']",
                ".jobs-apply-button",
                "button.jobs-s-apply-button"
            ]
            
            apply_button = None
            for selector in easy_apply_selectors:
                try:
                    if await page.locator(selector).is_visible(timeout=3000):
                        apply_button = selector
                        break
                except:
                    continue
            
            if not apply_button:
                return ApplyResult(
                    success=False,
                    platform='linkedin',
                    url=job_url,
                    message="No Easy Apply button found"
                )
            
            # Click Easy Apply
            await page.click(apply_button)
            await asyncio.sleep(2)  # Wait for modal
            
            # Process form steps
            steps_completed = 0
            fields_filled = 0
            skipped_fields = []
            
            while True:
                # Try to fill current step
                step_result = await self._fill_linkedin_step(page, resume_path)
                fields_filled += step_result['filled']
                skipped_fields.extend(step_result['skipped'])
                
                # Check for next/submit button
                next_selectors = [
                    "button:has-text('Next')",
                    "button:has-text('Submit application')",
                    "button[aria-label='Submit application']",
                    ".artdeco-button--primary"
                ]
                
                next_button = None
                for selector in next_selectors:
                    try:
                        if await page.locator(selector).is_visible(timeout=1000):
                            next_button = selector
                            break
                    except:
                        continue
                
                if not next_button:
                    break
                
                button_text = await page.locator(next_button).text_content()
                
                if 'Submit' in button_text:
                    # Final submit step - click and done
                    await page.click(next_button)
                    steps_completed += 1
                    await asyncio.sleep(2)
                    break
                else:
                    # Click next
                    await page.click(next_button)
                    steps_completed += 1
                    await asyncio.sleep(1.5)
            
            # Record successful application
            self.rate_limiter.record_application(
                url=job_url,
                success=True
            )
            
            await browser.close()
            await pw.stop()
            
            return ApplyResult(
                success=True,
                platform='linkedin',
                url=job_url,
                message=f"Applied successfully! {steps_completed} steps completed",
                form_filled=fields_filled,
                fields_skipped=skipped_fields
            )
            
        except Exception as e:
            return ApplyResult(
                success=False,
                platform='linkedin',
                url=job_url,
                message=f"Error: {str(e)}"
            )
    
    async def _fill_linkedin_step(self, page, resume_path: str) -> Dict[str, Any]:
        """Fill current form step with smart field detection"""
        filled = 0
        skipped = []
        
        # Common field mappings for LinkedIn
        field_patterns = {
            'phone': r'phone|phone_number|tel|mobile',
            'email': r'email|e-mail',
            'name': r'first.?name|last.?name|full.?name',
            'linkedin': r'linkedin|profile',
            'github': r'github|github.com',
            'portfolio': r'portfolio|website|url',
            'experience': r'years?.?of?.?experience',
            'salary': r'salary|compensation|expected',
            'notice': r'notice.?period|availability|start.?date',
        }
        
        # Find all visible input fields
        input_fields = page.locator("input:not([type='hidden']):not([type='submit'])")
        selects = page.locator("select")
        
        for field in await input_fields.all():
            try:
                name = await field.get_attribute('name') or ''
                id_attr = await field.get_attribute('id') or ''
                placeholder = await field.get_attribute('placeholder') or ''
                aria_label = await field.get_attribute('aria-label') or ''
                
                field_text = f"{name} {id_attr} {placeholder} {aria_label}".lower()
                
                # Skip resume upload (handled separately)
                if 'resume' in field_text or 'file' in field_text:
                    continue
                
                # Smart field detection
                matched = False
                for field_type, pattern in field_patterns.items():
                    if re.search(pattern, field_text):
                        matched = True
                        if field_type in ['linkedin', 'github', 'portfolio']:
                            skipped.append(field_type)
                        break
                        
            except Exception:
                continue
        
        # Handle resume upload
        try:
            resume_inputs = page.locator("input[type='file']")
            if await resume_inputs.count() > 0:
                await resume_inputs.first.set_input_files(resume_path)
                filled += 1
        except Exception:
            skipped.append('resume_upload')
        
        # Handle dropdowns - select "No" for optional questions
        for select in await selects.all():
            try:
                options = await select.locator("option").all()
                if len(options) > 1:
                    for option in options:
                        text = await option.text_content()
                        if text and 'no' in text.lower():
                            value = await option.get_attribute('value')
                            if value:
                                await select.select_option(value)
                                filled += 1
                                break
            except Exception:
                continue
        
        return {'filled': filled, 'skipped': skipped}
    
    async def apply_greenhouse(self, job_url: str, resume_path: str) -> ApplyResult:
        """Apply to Greenhouse job board"""
        self._platform = 'greenhouse'
        
        can_apply, reason = self.rate_limiter.can_apply(job_url)
        if not can_apply:
            return ApplyResult(
                success=False,
                platform='greenhouse',
                url=job_url,
                message=f"Rate limited: {reason}",
                guardrail_triggered=True
            )
        
        try:
            page = self._stealth.page
            
            await self._stealth.wait_for_page_load()
            await page.goto(job_url, timeout=self.config.timeout)
            await asyncio.sleep(2)
            
            apply_selectors = [
                "a:has-text('Apply')",
                "button:has-text('Apply')",
                "[data-qa='apply-button']"
            ]
            
            apply_button = None
            for selector in apply_selectors:
                try:
                    if await page.locator(selector).is_visible(timeout=3000):
                        apply_button = selector
                        break
                except:
                    continue
            
            if not apply_button:
                return ApplyResult(
                    success=False,
                    platform='greenhouse',
                    url=job_url,
                    message="No Apply button found"
                )
            
            await self._stealth.human_click(apply_button)
            await asyncio.sleep(2)
            
            fields_filled = await self._fill_generic_form(resume_path)
            
            submit_selectors = [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Submit')"
            ]
            
            for selector in submit_selectors:
                try:
                    if await page.locator(selector).is_visible(timeout=2000):
                        await self._stealth.human_click(selector)
                        break
                except:
                    continue
            
            self.rate_limiter.record_application(url=job_url, success=True)
            
            return ApplyResult(
                success=True,
                platform='greenhouse',
                url=job_url,
                message="Applied via Greenhouse",
                form_filled=fields_filled
            )
            
        except Exception as e:
            return ApplyResult(
                success=False,
                platform='greenhouse',
                url=job_url,
                message=f"Error: {str(e)}"
            )
    
    async def apply_lever(self, job_url: str, resume_path: str) -> ApplyResult:
        """Apply via Lever"""
        self._platform = 'lever'
        
        can_apply, reason = self.rate_limiter.can_apply(job_url)
        if not can_apply:
            return ApplyResult(
                success=False,
                platform='lever',
                url=job_url,
                message=f"Rate limited: {reason}",
                guardrail_triggered=True
            )
        
        try:
            page = self._stealth.page
            
            await self._stealth.wait_for_page_load()
            await page.goto(job_url, timeout=self.config.timeout)
            await asyncio.sleep(2)
            
            apply_button = page.locator("button.postings-btn-submit")
            if await apply_button.is_visible():
                await self._stealth.human_click("button.postings-btn-submit")
                await asyncio.sleep(2)
            
            fields_filled = await self._fill_generic_form(resume_path)
            
            submit = page.locator("button[type='submit']")
            if await submit.is_visible():
                await self._stealth.human_click("button[type='submit']")
            
            self.rate_limiter.record_application(url=job_url, success=True)
            
            return ApplyResult(
                success=True,
                platform='lever',
                url=job_url,
                message="Applied via Lever",
                form_filled=fields_filled
            )
            
        except Exception as e:
            return ApplyResult(
                success=False,
                platform='lever',
                url=job_url,
                message=f"Error: {str(e)}"
            )
    
    async def _fill_generic_form(self, resume_path: str) -> int:
        """Fill generic application form"""
        page = self._stealth.page
        filled = 0
        
        try:
            file_inputs = page.locator("input[type='file']")
            for inp in await file_inputs.all():
                accept = await inp.get_attribute('accept') or ''
                if 'pdf' in accept or 'resume' in accept.lower():
                    await inp.set_input_files(resume_path)
                    filled += 1
                    break
        except:
            pass
        
        return filled
    
    async def apply(self, job_url: str, resume_path: str) -> ApplyResult:
        """Apply based on detected platform"""
        platform = self._detect_platform(job_url)
        
        if platform == 'linkedin':
            return await self.apply_linkedin(job_url, resume_path)
        elif platform == 'greenhouse':
            return await self.apply_greenhouse(job_url, resume_path)
        elif platform == 'lever':
            return await self.apply_lever(job_url, resume_path)
        else:
            return ApplyResult(
                success=False,
                platform=platform,
                url=job_url,
                message=f"Platform {platform} not supported for auto-apply"
            )


# Backwards compatibility alias
BrowserApplyAgent = SmartBrowserApplyAgent

# CLI entry point
async def run_browser_apply(job_url: str, resume_path: str, use_stealth: bool = True) -> Dict[str, Any]:
    """Run browser-based application with stealth mode"""
    async with SmartBrowserApplyAgent() as agent:
        result = await agent.apply(job_url, resume_path)
        return {
            "success": result.success,
            "platform": result.platform,
            "url": result.url,
            "message": result.message,
            "fields_filled": result.form_filled,
            "skipped": result.fields_skipped,
            "guardrail_triggered": result.guardrail_triggered
        }
