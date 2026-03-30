#!/usr/bin/env python3
"""Generate tailored resumes for jobs discovered in jobspy discovery."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from job_agent.tailoring import analyze_job_url
from job_agent.models import FetchMode


# Jobs from jobspy discovery that are recommended for "apply"
JOBS = [
    # "apply" recommendations
    ("https://www.linkedin.com/jobs/view/4390932569", "Groww", "Web Developer (SDE 1)"),
    ("https://www.linkedin.com/jobs/view/4375231293", "PayPal", "Software Engineer - Backend"),
    ("https://www.linkedin.com/jobs/view/4379920789", "Cisco", "Software Engineer"),
    ("https://www.linkedin.com/jobs/view/4346770001", "Microsoft", "Software Engineer"),
    ("https://www.linkedin.com/jobs/view/4364800054", "Microsoft", "Software Engineer"),
    ("https://www.linkedin.com/jobs/view/4346206476", "Microsoft", "Software Engineer"),
    ("https://www.linkedin.com/jobs/view/4366861071", "Microsoft", "Software Engineer"),
    ("https://www.linkedin.com/jobs/view/4383717745", "Microsoft", "Software Engineer"),
    ("https://www.linkedin.com/jobs/view/4349300978", "Microsoft", "Software Engineer"),
    ("https://www.linkedin.com/jobs/view/4371847705", "Microsoft", "Software Engineer"),
    ("https://www.linkedin.com/jobs/view/4328738789", "Microsoft", "Software Engineer 2"),
    ("https://www.linkedin.com/jobs/view/4361627860", "Cisco", "Software Engineer"),
    ("https://www.linkedin.com/jobs/view/4376899612", "Cisco", "Software Engineer"),
    ("https://www.linkedin.com/jobs/view/4084261319", "DoorDash", "Software Engineer, Backend"),
    ("https://www.linkedin.com/jobs/view/4385350954", "Cisco", "Software Engineer"),
    ("https://www.linkedin.com/jobs/view/4238429280", "NxtWave", "Software Engineer"),
]

# "review" recommendations
REVIEW_JOBS = [
    ("https://www.linkedin.com/jobs/view/4385603096", "Flipkart", "Software Engineer II"),
    ("https://www.linkedin.com/jobs/view/4331964856", "Intuit", "Software Engineer 1"),
    ("https://www.linkedin.com/jobs/view/4382067579", "PayPal", "Software Engineer - Full Stack"),
    ("https://www.linkedin.com/jobs/view/4361354520", "Microsoft", "Software Engineer"),
]

OUTPUT_DIR = Path("outputs/jobs")


def main():
    print("=" * 60)
    print("Generating tailored resumes for discovered jobs...")
    print("=" * 60)
    
    all_jobs = JOBS + REVIEW_JOBS
    results = []
    
    for i, (url, company, title) in enumerate(all_jobs, 1):
        print(f"\n[{i}/{len(all_jobs)}] Analyzing: {company} - {title}")
        print(f"    URL: {url}")
        
        try:
            report = analyze_job_url(url, FetchMode.AUTO, OUTPUT_DIR, use_agents=False)
            
            result = {
                "company": company,
                "title": title,
                "url": url,
                "lane": report.decision.chosen_lane.value if report.decision.chosen_lane else "none",
                "ats_score": report.decision.ats_score,
                "recommendation": report.decision.final_recommendation,
                "tex_path": str(report.tailored_resume_path),
                "pdf_path": str(report.tailored_pdf_path) if report.tailored_pdf_path else None,
                "matched_keywords": report.decision.matched_keywords[:5],
                "missing_requirements": report.decision.missing_requirements[:3],
            }
            results.append(result)
            
            print(f"    ✅ ATS Score: {report.decision.ats_score}")
            print(f"    ✅ Recommendation: {report.decision.final_recommendation}")
            print(f"    ✅ Resume: {report.tailored_resume_path}")
            
        except Exception as e:
            print(f"    ❌ Error: {e}")
            results.append({
                "company": company,
                "title": title,
                "url": url,
                "error": str(e),
            })
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    successful = [r for r in results if "error" not in r]
    failed = [r for r in results if "error" in r]
    
    print(f"\nTotal: {len(all_jobs)} | Successful: {len(successful)} | Failed: {len(failed)}")
    
    # Group by ATS score
    print("\n--- Top Matches (ATS > 70) ---")
    for r in sorted(successful, key=lambda x: x.get("ats_score", 0), reverse=True):
        if r.get("ats_score", 0) > 70:
            print(f"  [{r['ats_score']}] {r['company']} - {r['title']}")
    
    print("\n--- Good Matches (ATS 55-70) ---")
    for r in sorted(successful, key=lambda x: x.get("ats_score", 0), reverse=True):
        if 55 <= r.get("ats_score", 0) <= 70:
            print(f"  [{r['ats_score']}] {r['company']} - {r['title']}")
    
    print("\n--- Review Needed (ATS < 55) ---")
    for r in sorted(successful, key=lambda x: x.get("ats_score", 0), reverse=True):
        if r.get("ats_score", 0) < 55:
            print(f"  [{r['ats_score']}] {r['company']} - {r['title']}")
    
    if failed:
        print("\n--- Failed ---")
        for r in failed:
            print(f"  ❌ {r['company']} - {r['title']}: {r.get('error')}")
    
    # Save results
    import json
    results_path = OUTPUT_DIR / "resume_generation_results.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n📄 Results saved to: {results_path}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
