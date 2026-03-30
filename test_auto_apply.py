"""Test auto-apply flow"""
import sys
sys.path.insert(0, 'src')

from job_agent.auto_apply import AutoApplyAgent

agent = AutoApplyAgent(
    resume_path='C:/Users/omkar/OneDrive/Desktop/job-agent/outputs/jobs/www-linkedin-com/software-engineer/resume_fullstack_backend.pdf',
    email='omkarchalak489@gmail.com'
)

# Test platform detection
test_urls = [
    'https://www.linkedin.com/jobs/view/4349300978',  # LinkedIn
    'https://boards.greenhouse.io/stripe/jobs/123',  # Greenhouse
    'https://jobs.lever.co/vercel/123',  # Lever
    'https://apply.workday.com/microsoft/jobs/123',  # Workday
    'https://careers.microsoft.com/us/en/jobs/123',  # Microsoft Careers
    'https://jobs.microsoft.com/job/456',  # Microsoft Jobs
]

print("Platform Detection Test:")
print("=" * 50)
for url in test_urls:
    platform = agent.detect_platform(url)
    info = agent.get_platform_info(url)
    short_url = url[:50] + "..." if len(url) > 50 else url
    print(f"URL: {short_url}")
    print(f"  Platform: {platform.value}")
    print(f"  Priority: {info['priority']} (1=highest, 8=lowest)")
    print()

print("\nLinkedIn External Apply Test:")
print("=" * 50)
import asyncio
result = asyncio.run(agent.apply(
    'https://www.linkedin.com/jobs/view/4349300978',
    {'title': 'Software Engineer', 'company': 'Microsoft'}
))
print(f"Success: {result.success}")
print(f"Platform: {result.platform.value}")
print(f"Message: {result.message}")
print(f"URL: {result.url}")
