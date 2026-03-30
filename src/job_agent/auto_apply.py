"""
Auto-Apply Agent - Platform detection with direct portal priority
Strategic focus on high-ROI direct portals over Easy Apply
"""
import re
import asyncio
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum

from job_agent.rate_limiter import RateLimiter, get_rate_limiter


class Platform(Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    WORKDAY = "workday"
    MICROSOFT = "microsoft"  # Microsoft Careers portal
    LINKEDIN_EASY = "linkedin_easy"  # Easy Apply - lower priority
    LINKEDIN_EXTERNAL = "linkedin_external"  # External apply - medium priority
    INDEED = "indeed"
    OTHER = "other"


@dataclass
class ApplyResult:
    success: bool
    platform: Platform
    application_id: Optional[str] = None
    message: str = ""
    url: Optional[str] = None
    priority: int = 5  # 1=highest, 5=lowest
    guardrail_triggered: bool = False


class AutoApplyAgent:
    """
    Auto-apply agent with strategic portal prioritization.
    
    Priority order (highest ROI first):
    1. Greenhouse - Direct ATS, lower competition
    2. Lever - Direct ATS, good for startups
    3. Ashby - Modern, direct ATS
    4. Workday - Enterprise, direct ATS
    5. LinkedIn External - Direct to company (medium)
    6. LinkedIn Easy Apply - Easy but crowded
    7. Indeed - Lower ROI
    """
    
    # Priority mapping (lower = higher priority)
    PLATFORM_PRIORITY = {
        Platform.GREENHOUSE: 1,
        Platform.LEVER: 2,
        Platform.ASHBY: 3,
        Platform.MICROSOFT: 3,  # High priority - direct portal
        Platform.WORKDAY: 4,
        Platform.LINKEDIN_EXTERNAL: 5,
        Platform.LINKEDIN_EASY: 6,
        Platform.INDEED: 7,
        Platform.OTHER: 8,
    }
    
    def __init__(self, resume_path: str, email: str, rate_limiter: Optional[RateLimiter] = None):
        self.resume_path = resume_path
        self.email = email
        self.rate_limiter = rate_limiter or get_rate_limiter()
        self._platform_patterns = {
            Platform.GREENHOUSE: r"boards\.greenhouse\.io",
            Platform.LEVER: r"jobs\.lever\.co",
            Platform.ASHBY: r"ashbyhq\.com|jobs\.ashbyhq\.com",
            Platform.WORKDAY: r"workday\.com|myworkdayjobs\.com",
            Platform.MICROSOFT: r"careers\.microsoft\.com|jobs\.microsoft\.com",
            Platform.LINKEDIN_EASY: r"linkedin\.com/jobs.*(?:easy|apply)",
            Platform.LINKEDIN_EXTERNAL: r"linkedin\.com/jobs/(?!.*(?:easy|apply))",
            Platform.INDEED: r"indeed\.com",
        }
    
    def detect_platform(self, url: str) -> Platform:
        """Detect application platform from URL"""
        url_lower = url.lower()
        for platform, pattern in self._platform_patterns.items():
            if re.search(pattern, url_lower, re.IGNORECASE):
                return platform
        return Platform.OTHER
    
    def get_priority(self, platform: Platform) -> int:
        """Get application priority (lower = higher priority)"""
        return self.PLATFORM_PRIORITY.get(platform, 8)
    
    def get_platform_info(self, url: str) -> Dict[str, Any]:
        """Get detailed platform info for a URL"""
        platform = self.detect_platform(url)
        priority = self.get_priority(platform)
        
        # Get rate limit status
        status = self.rate_limiter.get_status(url)
        can_apply = status.can_apply
        reason = "OK" if can_apply else f"Rate limited: {status.daily_count}/{status.daily_max}"
        
        return {
            "platform": platform.value,
            "priority": priority,
            "can_apply": can_apply,
            "reason": reason,
            "daily_count": status.daily_count,
            "daily_max": status.daily_max,
            "guardrail_triggered": not can_apply,
        }
    
    async def apply(self, job_url: str, job_data: Dict[str, Any]) -> ApplyResult:
        """Apply to job based on detected platform"""
        platform = self.detect_platform(job_url)
        priority = self.get_priority(platform)
        
        # Check rate limits first
        can_apply, reason = self.rate_limiter.can_apply(job_url)
        if not can_apply:
            return ApplyResult(
                success=False,
                platform=platform,
                message=f"Rate limited: {reason}",
                url=job_url,
                priority=priority,
                guardrail_triggered=True
            )
        
        apply_methods = {
            Platform.GREENHOUSE: self._apply_greenhouse,
            Platform.LEVER: self._apply_lever,
            Platform.ASHBY: self._apply_ashby,
            Platform.MICROSOFT: self._apply_microsoft,
            Platform.WORKDAY: self._apply_workday,
            Platform.LINKEDIN_EASY: self._apply_linkedin,
            Platform.LINKEDIN_EXTERNAL: self._apply_linkedin,
            Platform.INDEED: self._apply_indeed,
            Platform.OTHER: self._apply_other,
        }
        
        method = apply_methods.get(platform, self._apply_other)
        result = await method(job_url, job_data)
        result.priority = priority
        
        # Record application
        self.rate_limiter.record_application(
            url=job_url,
            company=job_data.get("company", ""),
            title=job_data.get("title", ""),
            success=result.success
        )
        
        return result
    
    async def _apply_greenhouse(self, url: str, job_data: Dict) -> ApplyResult:
        """Apply via Greenhouse - HIGHEST ROI"""
        match = re.search(r"boards\.greenhouse\.io/([^/]+)", url)
        if not match:
            return ApplyResult(False, Platform.GREENHOUSE, message="Invalid Greenhouse URL")
        
        apply_url = url.replace("/jobs/", "/jobs/apply/")
        
        return ApplyResult(
            success=True,
            platform=Platform.GREENHOUSE,
            message=f"Greenhouse apply ready: {apply_url}",
            url=apply_url,
            application_id=match.group(1)
        )
    
    async def _apply_lever(self, url: str, job_data: Dict) -> ApplyResult:
        """Apply via Lever - HIGH ROI"""
        match = re.search(r"jobs\.lever\.co/([^/]+)/([^/]+)", url)
        if match:
            apply_url = f"https://jobs.lever.co/{match.group(1)}/{match.group(2)}/apply"
        else:
            apply_url = url.rstrip("/") + "/apply"
        
        return ApplyResult(
            success=True,
            platform=Platform.LEVER,
            message=f"Lever apply ready: {apply_url}",
            url=apply_url
        )
    
    async def _apply_ashby(self, url: str, job_data: Dict) -> ApplyResult:
        """Apply via Ashby - HIGH ROI"""
        apply_url = url.rstrip("/") + "/apply"
        
        return ApplyResult(
            success=True,
            platform=Platform.ASHBY,
            message=f"Ashby apply ready: {apply_url}",
            url=apply_url
        )
    
    async def _apply_microsoft(self, url: str, job_data: Dict) -> ApplyResult:
        """Microsoft Careers - HIGH ROI (direct portal)"""
        # Microsoft uses their own careers portal
        # Try to convert to apply URL
        if "/job/" in url:
            job_id = re.search(r'/job/(\d+)', url)
            if job_id:
                apply_url = f"https://careers.microsoft.com/us/en/apply?jobId={job_id.group(1)}"
            else:
                apply_url = url
        else:
            apply_url = url
        
        return ApplyResult(
            success=True,
            platform=Platform.MICROSOFT,
            message="Microsoft Careers portal detected. Use pilot-mcp for browser automation.",
            url=apply_url,
            priority=3
        )
    
    async def _apply_workday(self, url: str, job_data: Dict) -> ApplyResult:
        """Workday - MEDIUM ROI (enterprise, direct ATS)"""
        return ApplyResult(
            success=True,
            platform=Platform.WORKDAY,
            message="Workday portal detected. Use browser for application.",
            url=url
        )
    
    async def _apply_linkedin(self, url: str, job_data: Dict) -> ApplyResult:
        """LinkedIn - MEDIUM/LOW ROI depending on Easy Apply vs External"""
        platform = self.detect_platform(url)
        
        if platform == Platform.LINKEDIN_EASY:
            return ApplyResult(
                success=True,
                platform=Platform.LINKEDIN_EASY,
                message="LinkedIn Easy Apply detected. Use browser-apply for automation.",
                url=url,
                priority=6
            )
        else:
            return ApplyResult(
                success=True,
                platform=Platform.LINKEDIN_EXTERNAL,
                message="LinkedIn external posting. Apply via company portal.",
                url=url,
                priority=5
            )
    
    async def _apply_indeed(self, url: str, job_data: Dict) -> ApplyResult:
        """Indeed - LOWER ROI"""
        return ApplyResult(
            success=True,
            platform=Platform.INDEED,
            message="Indeed posting. Apply via direct URL.",
            url=url,
            priority=7
        )
    
    async def _apply_other(self, url: str, job_data: Dict) -> ApplyResult:
        """Other platforms"""
        company = job_data.get("company", "Unknown")
        title = job_data.get("title", "Unknown Position")
        
        return ApplyResult(
            success=True,
            platform=Platform.OTHER,
            message=f"Apply to {company} - {title}",
            url=url,
            priority=8
        )
    
    def prioritize_jobs(self, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Reorder jobs by strategic priority."""
        def job_priority(job: Dict) -> tuple:
            url = job.get("url", "")
            platform = self.detect_platform(url)
            base_priority = self.get_priority(platform)
            ats_score = job.get("ats_score", 50)
            return (base_priority, -ats_score)
        
        return sorted(jobs, key=job_priority)


async def auto_apply_batch(
    jobs: List[Dict[str, Any]],
    resume_path: str,
    email: str,
    min_ats: float = 80,
    max_per_platform: int = None
) -> List[ApplyResult]:
    """Apply to multiple jobs with strategic prioritization"""
    agent = AutoApplyAgent(resume_path, email)
    results = []
    
    prioritized_jobs = agent.prioritize_jobs(jobs)
    platform_counts: Dict[str, int] = {}
    
    for job in prioritized_jobs:
        ats_score = job.get("ats_score", 0)
        if ats_score < min_ats:
            continue
        
        url = job.get("url", "")
        platform = agent.detect_platform(url)
        platform_name = platform.value
        
        if max_per_platform:
            count = platform_counts.get(platform_name, 0)
            if count >= max_per_platform:
                continue
            platform_counts[platform_name] = count + 1
        
        result = await agent.apply(url, job)
        results.append(result)
        
        if result.success and not result.guardrail_triggered:
            delay = agent.rate_limiter.get_delay_minutes()
            await asyncio.sleep(delay * 60)
    
    return results


def get_rate_limit_summary() -> Dict[str, Any]:
    """Get current rate limit status for all platforms"""
    limiter = get_rate_limiter()
    return limiter.get_summary()
