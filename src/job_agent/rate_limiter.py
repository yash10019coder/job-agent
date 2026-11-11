"""
Rate Limiter - Anti-Bot Guardrails
Manages application pacing to avoid shadow banning
"""
import time
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from enum import Enum
import random


class Platform(Enum):
    LINKEDIN = "linkedin"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    WORKDAY = "workday"
    ASHBY = "ashby"
    INDEED = "indeed"
    OTHER = "other"


@dataclass
class RateLimitConfig:
    """Rate limits per platform (per day)"""
    linkedin_max: int = 20
    greenhouse_max: int = 15
    lever_max: int = 15
    workday_max: int = 10
    ashby_max: int = 15
    indeed_max: int = 25
    
    # Inter-application delays (minutes)
    min_delay_minutes: int = 15
    max_delay_minutes: int = 45
    
    # Hourly caps
    hourly_max: int = 4
    
    # Grace period after rate limit hit (hours)
    grace_period_hours: int = 12


@dataclass
class ApplicationRecord:
    """Record of a single application"""
    timestamp: str
    platform: str
    url: str
    company: str
    title: str
    success: bool


@dataclass
class RateLimitStatus:
    """Current status of rate limiting"""
    platform: str
    daily_count: int
    daily_max: int
    hourly_count: int
    hourly_max: int
    next_available: Optional[str] = None
    in_grace_period: bool = False
    can_apply: bool = True
    
    def __str__(self):
        remaining = max(0, self.daily_max - self.daily_count)
        status = "✅ CAN APPLY" if self.can_apply else "🚫 RATE LIMITED"
        if self.next_available:
            return f"{status} | {self.platform}: {self.daily_count}/{self.daily_max} today | Next: {self.next_available}"
        return f"{status} | {self.platform}: {self.daily_count}/{self.daily_max} today ({remaining} remaining)"


class RateLimiter:
    """
    Manages application pacing to avoid bot detection and shadow banning.
    Tracks applications per platform and enforces daily/hourly limits.
    """
    
    def __init__(
        self,
        config: Optional[RateLimitConfig] = None,
        storage_path: Optional[Path] = None
    ):
        self.config = config or RateLimitConfig()
        self.storage_path = storage_path or Path("outputs/rate_limiter/history.jsonl")
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._history: List[ApplicationRecord] = []
        self._last_apply_time: Dict[str, float] = {}
        self._load_history()
    
    def _load_history(self):
        """Load application history from storage"""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r') as f:
                    for line in f:
                        if line.strip():
                            data = json.loads(line)
                            self._history.append(ApplicationRecord(**data))
                # Clean old entries (keep last 30 days)
                self._cleanup_old_entries()
            except Exception:
                self._history = []
    
    def _cleanup_old_entries(self):
        """Remove entries older than 30 days"""
        cutoff = datetime.now() - timedelta(days=30)
        self._history = [
            r for r in self._history
            if datetime.fromisoformat(r.timestamp) > cutoff
        ]
    
    def _save_record(self, record: ApplicationRecord):
        """Append record to storage"""
        self._history.append(record)
        try:
            with open(self.storage_path, 'a') as f:
                f.write(json.dumps(record.__dict__) + '\n')
        except Exception:
            pass  # Don't crash if write fails
    
    def detect_platform(self, url: str) -> Platform:
        """Detect platform from URL"""
        url_lower = url.lower()
        if 'linkedin.com' in url_lower:
            return Platform.LINKEDIN
        elif 'greenhouse.io' in url_lower:
            return Platform.GREENHOUSE
        elif 'lever.co' in url_lower:
            return Platform.LEVER
        elif 'workday' in url_lower:
            return Platform.WORKDAY
        elif 'ashbyhq' in url_lower:
            return Platform.ASHBY
        elif 'indeed' in url_lower:
            return Platform.INDEED
        return Platform.OTHER
    
    def _get_daily_max(self, platform: Platform) -> int:
        """Get daily max for platform"""
        limits = {
            Platform.LINKEDIN: self.config.linkedin_max,
            Platform.GREENHOUSE: self.config.greenhouse_max,
            Platform.LEVER: self.config.lever_max,
            Platform.WORKDAY: self.config.workday_max,
            Platform.ASHBY: self.config.ashby_max,
            Platform.INDEED: self.config.indeed_max,
            Platform.OTHER: 20,
        }
        return limits.get(platform, 20)
    
    def _get_today_records(self, platform: Platform) -> List[ApplicationRecord]:
        """Get today's application records for platform"""
        today = datetime.now().date()
        platform_val = platform.value if isinstance(platform, Platform) else platform
        return [
            r for r in self._history
            if r.platform == platform_val
            and datetime.fromisoformat(r.timestamp).date() == today
        ]
    
    def _get_hourly_records(self, platform: Platform) -> List[ApplicationRecord]:
        """Get last hour's application records for platform"""
        hour_ago = datetime.now() - timedelta(hours=1)
        platform_val = platform.value if isinstance(platform, Platform) else platform
        return [
            r for r in self._history
            if r.platform == platform_val
            and datetime.fromisoformat(r.timestamp) > hour_ago
        ]
    
    def get_status(self, url: str) -> RateLimitStatus:
        """Get current rate limit status for a URL"""
        platform = self.detect_platform(url)
        daily_records = self._get_today_records(platform)
        hourly_records = self._get_hourly_records(platform)
        
        daily_count = len(daily_records)
        hourly_count = len(hourly_records)
        daily_max = self._get_daily_max(platform)
        
        can_apply = True
        next_available = None
        in_grace_period = False
        
        # Check if daily limit reached
        if daily_count >= daily_max:
            # Find next available time (tomorrow midnight)
            tomorrow = datetime.now().replace(hour=0, minute=0, second=0) + timedelta(days=1)
            next_available = tomorrow.strftime("%Y-%m-%d 00:00")
            can_apply = False
            in_grace_period = True
        
        # Check if hourly limit reached
        if hourly_count >= self.config.hourly_max:
            # Wait for next hour
            next_hour = datetime.now().replace(minute=0, second=0) + timedelta(hours=1)
            next_available = next_hour.strftime("%Y-%m-%d %H:00")
            can_apply = False
        
        return RateLimitStatus(
            platform=platform.value,
            daily_count=daily_count,
            daily_max=daily_max,
            hourly_count=hourly_count,
            hourly_max=self.config.hourly_max,
            next_available=next_available,
            in_grace_period=in_grace_period,
            can_apply=can_apply,
        )
    
    def can_apply(self, url: str) -> tuple[bool, str]:
        """
        Check if we can apply to this URL.
        Returns (can_apply, reason)
        """
        status = self.get_status(url)
        
        if not status.can_apply:
            if status.in_grace_period:
                return False, f"Daily limit reached ({status.daily_count}/{status.daily_max}). Next available: {status.next_available}"
            else:
                return False, f"Hourly limit reached ({status.hourly_count}/{status.hourly_max}). Next available: {status.next_available}"
        
        # Check if recently applied to same company
        url_lower = url.lower()
        company_domain = self._extract_domain(url_lower)
        
        recent_same_company = [
            r for r in self._get_today_records(status.platform)
            if self._extract_domain(r.url.lower()) == company_domain
            and datetime.fromisoformat(r.timestamp) > datetime.now() - timedelta(hours=24)
        ]
        
        if recent_same_company:
            return False, f"Already applied to {company_domain} recently"
        
        return True, "OK"
    
    # Multi-tenant job boards: the host is shared by every company, so the first path segment is the tenant.
    _TENANT_HOSTS = {"job-boards.greenhouse.io", "boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
                     "apply.workable.com", "jobs.smartrecruiters.com", "jobs.eu.lever.co"}

    def _extract_domain(self, url: str) -> str:
        """Identify the company site: the host, plus the tenant segment on shared job boards.

        (Host alone made one Greenhouse application block every other Greenhouse company for 24h.)"""
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
            host = parsed.netloc
            if host in self._TENANT_HOSTS:
                tenant = next((seg for seg in parsed.path.split("/") if seg), "")
                return f"{host}/{tenant}" if tenant else host
            return host
        except Exception:
            return url
    
    def record_application(
        self,
        url: str,
        company: str = "",
        title: str = "",
        success: bool = True
    ) -> ApplicationRecord:
        """Record a new application"""
        platform = self.detect_platform(url)
        record = ApplicationRecord(
            timestamp=datetime.now().isoformat(),
            platform=platform.value,
            url=url,
            company=company,
            title=title,
            success=success,
        )
        
        self._save_record(record)
        self._last_apply_time[platform.value] = time.time()
        
        return record
    
    async def wait_for_next_slot(self, url: str) -> float:
        """
        Wait for the next available slot.
        Returns actual wait time in seconds.
        """
        status = self.get_status(url)
        
        if status.can_apply:
            # Still check minimum delay between applications
            platform = self.detect_platform(url)
            last_time = self._last_apply_time.get(platform.value, 0)
            
            if last_time > 0:
                elapsed = time.time() - last_time
                min_delay_seconds = self.config.min_delay_minutes * 60
                max_delay_seconds = self.config.max_delay_minutes * 60
                
                # Add randomness
                delay = random.uniform(min_delay_seconds, max_delay_seconds)
                
                if elapsed < delay:
                    wait_time = delay - elapsed
                    import asyncio
                    await asyncio.sleep(wait_time)
                    return wait_time
        
        return 0.0
    
    def get_delay_minutes(self) -> int:
        """Get random delay between applications"""
        return random.randint(
            self.config.min_delay_minutes,
            self.config.max_delay_minutes
        )
    
    def get_summary(self) -> Dict:
        """Get summary of rate limiting status"""
        today = datetime.now().date()
        today_records = [
            r for r in self._history
            if datetime.fromisoformat(r.timestamp).date() == today
        ]
        
        summary = {
            "total_today": len(today_records),
            "by_platform": {},
            "can_apply": True,
        }
        
        for platform in Platform:
            records = [r for r in today_records if r.platform == platform.value]
            max_allowed = self._get_daily_max(platform)
            remaining = max(0, max_allowed - len(records))
            
            summary["by_platform"][platform.value] = {
                "count": len(records),
                "max": max_allowed,
                "remaining": remaining,
                "can_apply": remaining > 0,
            }
            
            if remaining == 0:
                summary["can_apply"] = False
        
        return summary


# Global rate limiter instance
_global_limiter: Optional[RateLimiter] = None


def get_rate_limiter(config: Optional[RateLimitConfig] = None) -> RateLimiter:
    """Get or create global rate limiter instance"""
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = RateLimiter(config)
    return _global_limiter
