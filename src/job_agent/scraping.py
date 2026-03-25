from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import parse_qs, urljoin, urlparse

from scrapling import DynamicFetcher, Fetcher, Selector, StealthyFetcher

from job_agent.models import BoardCandidate, BoardDiscoveryReport, FetchMode, SourceType


_PLACEHOLDER_SLUGS = {
    "company",
    "example",
    "your-company",
    "your_company",
    "yourcompany",
    "your-account",
    "your_account",
    "youraccount",
    "youraccountname",
}

_JOB_QUERY_KEYS = {"id", "jobid", "job_id", "jvi", "gh_jid", "lever-via", "gh_src"}
_JOBS_HUB_PATH_TOKENS = {"/jobs", "/job", "/careers", "/positions", "/openings", "/opportunities", "/roles"}
_JOBS_HUB_TEXT_TOKENS = {
    "open roles",
    "open positions",
    "view open roles",
    "careers",
    "join us",
    "job search",
    "see all jobs",
    "opportunities",
}
_ROLE_TEXT_TOKENS = {
    "engineer",
    "developer",
    "backend",
    "full stack",
    "full-stack",
    "software",
    "platform",
    "systems",
    "sre",
    "site reliability",
    "intern",
    "staff",
    "senior",
    "lead",
    "manager",
    "scientist",
    "architect",
}
_BOARD_HOST_RULES = {
    "jobs.lever.co": ("lever", True),
    "boards.greenhouse.io": ("greenhouse", True),
    "myworkdayjobs.com": ("workday", False),
    "ashbyhq.com": ("ashby", False),
    "job-boards.greenhouse.io": ("greenhouse", True),
}
_BOARD_URL_PATTERN = re.compile(
    r"https?://(?:jobs\.lever\.co|boards\.greenhouse\.io|job-boards\.greenhouse\.io|[a-z0-9.-]+\.myworkdayjobs\.com|jobs\.ashbyhq\.com)[^\"'\s<>()]+",
    flags=re.IGNORECASE,
)


@dataclass
class FetchResult:
    url: str
    html: str
    mode: FetchMode


class ScraplingClient:
    """Resilient fetch wrapper around Scrapling's static, stealth, and dynamic modes."""

    def __init__(self) -> None:
        self._static = Fetcher
        self._stealth = StealthyFetcher
        self._dynamic = DynamicFetcher

    def fetch(self, url: str, mode: FetchMode = FetchMode.AUTO) -> FetchResult:
        self._validate_supported_url(url)
        if mode is FetchMode.STATIC:
            return self._fetch_static(url)
        if mode is FetchMode.STEALTH:
            return self._fetch_stealth(url)
        if mode is FetchMode.DYNAMIC:
            return self._fetch_dynamic(url)
        return self._fetch_auto(url)

    def _fetch_auto(self, url: str) -> FetchResult:
        strategies = [self._fetch_static, self._fetch_stealth, self._fetch_dynamic]
        last_error: Exception | None = None
        for strategy in strategies:
            try:
                result = strategy(url)
                if len(Selector(result.html).get_all_text(separator=" ", strip=True)) > 300:
                    return result
            except ValueError:
                raise
            except Exception as error:  # pragma: no cover - exercised by live usage
                if self._is_terminal_fetch_error(error):
                    raise RuntimeError(self._terminal_fetch_message(url, error)) from error
                last_error = error
        if last_error:
            raise last_error
        raise RuntimeError(f"Unable to fetch URL: {url}")

    def _fetch_static(self, url: str) -> FetchResult:
        response = self._static.get(url, verify=False)
        self._raise_for_http_error(url, response)
        return FetchResult(url=str(response.url), html=self._html(response), mode=FetchMode.STATIC)

    def _fetch_stealth(self, url: str) -> FetchResult:
        response = self._stealth.fetch(url, headless=True)
        self._raise_for_http_error(url, response)
        return FetchResult(url=str(response.url), html=self._html(response), mode=FetchMode.STEALTH)

    def _fetch_dynamic(self, url: str) -> FetchResult:
        response = self._dynamic.fetch(url, headless=True)
        self._raise_for_http_error(url, response)
        return FetchResult(url=str(response.url), html=self._html(response), mode=FetchMode.DYNAMIC)

    @staticmethod
    def _html(response) -> str:
        html = getattr(response, "html_content", None) or getattr(response, "text", "")
        if isinstance(html, bytes):
            return html.decode("utf-8", errors="ignore")
        return str(html)

    @staticmethod
    def _validate_supported_url(url: str) -> None:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        parts = [part.lower() for part in parsed.path.split("/") if part]
        if "jobs.lever.co" in host and parts and parts[0] in _PLACEHOLDER_SLUGS:
            raise ValueError(
                "Invalid Lever URL: the first path segment looks like a placeholder. "
                "Use a real company slug such as https://jobs.lever.co/netflix or "
                "https://jobs.lever.co/netflix/<job-id>."
            )
        if "greenhouse.io" in host and parts and parts[0] in _PLACEHOLDER_SLUGS:
            raise ValueError(
                "Invalid Greenhouse URL: the first path segment looks like a placeholder. "
                "Use a real board slug such as https://boards.greenhouse.io/stripe or "
                "https://boards.greenhouse.io/stripe/jobs/<job-id>."
            )

    @staticmethod
    def _raise_for_http_error(url: str, response) -> None:
        status = int(getattr(response, "status", 0) or getattr(response, "status_code", 0) or 0)
        if status < 400:
            return
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if status == 404 and "jobs.lever.co" in host:
            raise ValueError(
                "Lever returned 404 for this board URL. The company may not use a Lever-hosted board at this address. "
                "Try the company's own careers site instead, for example https://jobs.netflix.com/ rather than "
                "https://jobs.lever.co/netflix."
            )
        if status == 404 and "greenhouse.io" in host:
            raise ValueError(
                "Greenhouse returned 404 for this board URL. The board slug or job path may be wrong, or the company may "
                "not use a public Greenhouse board at this address."
            )
        raise ValueError(f"HTTP {status} while fetching {url}")

    @staticmethod
    def _is_terminal_fetch_error(error: Exception) -> bool:
        message = str(error).lower()
        return isinstance(error, PermissionError) or "failed to connect to" in message or "curl: (7)" in message

    @staticmethod
    def _terminal_fetch_message(url: str, error: Exception) -> str:
        if isinstance(error, PermissionError):
            return (
                f"Browser-backed fetch could not start for {url}. "
                "This environment is blocking the Playwright/Patchright subprocess."
            )
        return f"Network connection failed while fetching {url}. Check connectivity or try again from your normal terminal."

    def discover_job_links(
        self,
        search_url: str,
        mode: FetchMode = FetchMode.AUTO,
        limit: int = 25,
    ) -> list[str]:
        fetched = self.fetch(search_url, mode=mode)
        selector = Selector(fetched.html, url=fetched.url)
        links = self._extract_job_links(selector, fetched.url, search_url, limit)
        if links:
            return links
        if self._looks_like_job_posting(search_url):
            return [search_url]

        for hub_url in self._extract_hub_links(selector, fetched.url):
            hub_fetched = self.fetch(hub_url, mode=mode)
            hub_selector = Selector(hub_fetched.html, url=hub_fetched.url)
            links = self._extract_job_links(hub_selector, hub_fetched.url, search_url, limit)
            if links:
                return links
        for candidate_url in self._candidate_hub_urls(fetched.url):
            try:
                hub_fetched = self.fetch(candidate_url, mode=mode)
            except Exception:
                continue
            hub_selector = Selector(hub_fetched.html, url=hub_fetched.url)
            links = self._extract_job_links(hub_selector, hub_fetched.url, search_url, limit)
            if links:
                return links
        for candidate in self._discover_board_candidates_from_page(search_url, fetched):
            try:
                hub_fetched = self.fetch(candidate.url, mode=mode)
            except Exception:
                continue
            hub_selector = Selector(hub_fetched.html, url=hub_fetched.url)
            links = self._extract_job_links(hub_selector, hub_fetched.url, candidate.url, limit)
            if links:
                return links
        return []

    def discover_board_candidates(self, url: str, mode: FetchMode = FetchMode.AUTO) -> BoardDiscoveryReport:
        fetched = self.fetch(url, mode=mode)
        return BoardDiscoveryReport(
            input_url=url,
            fetch_mode=fetched.mode,
            candidates=self._discover_board_candidates_from_page(url, fetched),
        )

    def _extract_job_links(self, selector: Selector, base_url: str, parent_url: str, limit: int) -> list[str]:
        links: list[str] = []
        seen: set[str] = set()
        for anchor in selector.css("a"):
            href = anchor.attrib.get("href")
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            absolute = urljoin(base_url, href)
            text = anchor.get_all_text(separator=" ", strip=True)
            if absolute in seen or not self._looks_like_job_link(absolute, parent_url, text):
                continue
            seen.add(absolute)
            links.append(absolute)
            if len(links) >= limit:
                break
        return links

    def _extract_hub_links(self, selector: Selector, base_url: str) -> list[str]:
        hubs: list[str] = []
        seen: set[str] = set()
        for anchor in selector.css("a"):
            href = anchor.attrib.get("href")
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            absolute = urljoin(base_url, href)
            if absolute in seen or not self._looks_like_jobs_hub(absolute, anchor.get_all_text(separator=" ", strip=True), base_url):
                continue
            seen.add(absolute)
            hubs.append(absolute)
            if len(hubs) >= 3:
                break
        return hubs

    def _discover_board_candidates_from_page(self, original_url: str, fetched: FetchResult) -> list[BoardCandidate]:
        selector = Selector(fetched.html, url=fetched.url)
        candidates: dict[str, BoardCandidate] = {}
        for anchor in selector.css("a"):
            href = anchor.attrib.get("href")
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            absolute = urljoin(fetched.url, href)
            candidate = self._board_candidate_from_url(
                absolute,
                reason=f"Found linked careers/board URL on {urlparse(original_url).netloc or original_url}",
                anchor_text=anchor.get_all_text(separator=" ", strip=True),
            )
            if candidate:
                candidates.setdefault(candidate.url, candidate)
        for match in _BOARD_URL_PATTERN.findall(fetched.html):
            candidate = self._board_candidate_from_url(
                match,
                reason="Found embedded board URL in page markup",
                anchor_text="",
            )
            if candidate:
                candidates.setdefault(candidate.url, candidate)
        return sorted(candidates.values(), key=lambda item: item.confidence, reverse=True)

    def _board_candidate_from_url(self, url: str, reason: str, anchor_text: str) -> BoardCandidate | None:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        board_type = "company"
        supported = True
        for pattern, value in _BOARD_HOST_RULES.items():
            if pattern in host:
                board_type, supported = value
                break
        if board_type == "company":
            if not self._looks_like_jobs_hub(url, anchor_text, url) and not self._looks_like_job_posting(url, anchor_text):
                return None
        confidence = 0.55
        if board_type in {"lever", "greenhouse"}:
            confidence = 0.95
        elif board_type in {"workday", "ashby"}:
            confidence = 0.85
        elif self._looks_like_job_posting(url, anchor_text):
            confidence = 0.8
        elif self._looks_like_jobs_hub(url, anchor_text, url):
            confidence = 0.7
        return BoardCandidate(
            url=url,
            source=board_type,
            confidence=confidence,
            reason=reason,
            supported=supported,
        )

    @staticmethod
    def _candidate_hub_urls(base_url: str) -> list[str]:
        parsed = urlparse(base_url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        paths = ["/careers", "/jobs", "/careers/jobs", "/open-roles", "/openings"]
        return [f"{root}{path}" for path in paths if f"{root}{path}" != base_url.rstrip("/")]

    def _looks_like_job_link(self, url: str, parent_url: str, anchor_text: str = "") -> bool:
        if self._looks_like_job_posting(url, anchor_text):
            return True
        parent_host = urlparse(parent_url).netloc.lower()
        candidate = urlparse(url)
        if candidate.netloc.lower() != parent_host:
            return False
        path = candidate.path.lower()
        return any(token in path for token in ["/jobs/", "/job/", "/careers/", "/positions/", "/openings/"]) and (
            self._looks_like_role_text(anchor_text) or len([part for part in path.split("/") if part]) >= 3
        )

    @staticmethod
    def _looks_like_job_posting(url: str, anchor_text: str = "") -> bool:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        query_keys = {key.lower() for key in parse_qs(parsed.query)}
        path_parts = [part for part in path.split("/") if part]
        if "greenhouse.io" in host:
            return "/jobs/" in path
        if "lever.co" in host:
            return len(path_parts) >= 2 and path_parts[-1] not in {"jobs", "careers"}
        if query_keys & _JOB_QUERY_KEYS and (ScraplingClient._looks_like_role_text(anchor_text) or "jvi" in query_keys):
            return True
        return any(token in path for token in ["/jobs/", "/job/", "/careers/", "/positions/", "/openings/"]) and (
            ScraplingClient._looks_like_role_text(anchor_text) or len(path_parts) >= 3
        )

    @staticmethod
    def _looks_like_jobs_hub(url: str, anchor_text: str, parent_url: str) -> bool:
        candidate = urlparse(url)
        parent_host = urlparse(parent_url).netloc.lower()
        if candidate.netloc.lower() != parent_host:
            return False
        path = candidate.path.lower()
        text = anchor_text.strip().lower()
        return any(token in path for token in _JOBS_HUB_PATH_TOKENS) or any(token in text for token in _JOBS_HUB_TEXT_TOKENS)

    @staticmethod
    def _looks_like_role_text(text: str) -> bool:
        lowered = text.lower()
        return any(token in lowered for token in _ROLE_TEXT_TOKENS)


def detect_board_type(url: str) -> SourceType:
    host = urlparse(url).netloc.lower()
    if "greenhouse.io" in host:
        return SourceType.GREENHOUSE
    if "lever.co" in host:
        return SourceType.LEVER
    return SourceType.COMPANY
