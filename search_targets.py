"""Search target companies and categorize jobs"""
import asyncio
import sys
import json
from pathlib import Path
sys.path.insert(0, 'src')
from job_agent.jobspy_adapter import search_jobs

async def main():
    companies = ['Microsoft', 'Google', 'Amazon', 'Atlassian', 'Adobe', 'Qualcomm', 'BrowserStack', 'Meta', 'Apple']
    all_jobs = []
    
    for company in companies:
        try:
            print(f'Searching {company}...')
            report = search_jobs(
                search_term=f'{company} Software Engineer',
                location='India',
                sites=['linkedin'],
                results_wanted=5,
                output_dir=Path(f'outputs/search/{company.lower()}')
            )
            print(f'  Found {len(report.results)} jobs')
            all_jobs.extend([r.model_dump() for r in report.results])
            await asyncio.sleep(2)
        except Exception as e:
            print(f'  Error: {e}')
    
    print(f'\n=== Total: {len(all_jobs)} jobs ===')
    
    # Save to file
    with open('outputs/target_company_jobs.json', 'w') as f:
        json.dump(all_jobs, f, indent=2, default=str)
    
    # Categorize by platform
    auto_apply = []
    manual_resume = []
    
    for job in all_jobs:
        url = str(job.get('url', '')).lower()
        if 'greenhouse' in url or 'lever.co' in url or 'ashby' in url:
            auto_apply.append(job)
        else:
            manual_resume.append(job)
    
    print(f'\nAUTO-APPLY (Greenhouse/Lever/Ashby): {len(auto_apply)}')
    for j in auto_apply:
        print(f'  - {j.get("company")}: {j.get("title")}')
        print(f'    {j.get("url")}')
    
    print(f'\nMANUAL+RESUME (LinkedIn/Indeed): {len(manual_resume)}')
    for j in manual_resume[:10]:
        print(f'  - {j.get("company")}: {j.get("title")}')
        print(f'    {j.get("url")}')

if __name__ == '__main__':
    asyncio.run(main())
