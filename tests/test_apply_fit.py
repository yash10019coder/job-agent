"""The job-fit gate: apply only to roles that fit (software/AI/data/platform engineering, up to senior, India or open-remote)."""
from job_agent.apply.fit import FitDecision, fit


def job(title, location="Bengaluru, India", company="Acme"):
    return {"title": title, "location": location, "company": company}


def test_engineering_roles_in_india_pass():
    for title in ("Senior Backend Engineer, Development Health", "Software Engineer II", "Data Engineer III", "AI Engineer (LLM platform)",
                  "Full Stack Developer", "SDE-2 Backend", "Site Reliability Engineer, Cells Infrastructure"):
        assert fit(job(title)).ok, title


def test_wrong_function_or_level_is_rejected_with_a_reason():
    for title in ("Principal Software Engineer", "Engineering Manager, Platform", "Director of Engineering", "Software Engineer Intern",
                  "Sales Engineer", "Mechanical Engineer", "Recruiter, R&D", "Staff Backend Engineer", "Field Engineer, Life Sciences", "Forward Deployed Engineer, AI Products",
                  "Senior Network Deployment Engineer", "Specialist Solution Engineer 7", "Software Engineer, New Grad 2027", "Test Engineer"):
        d = fit(job(title))
        assert not d.ok and d.reasons, title


def test_location_rules():
    assert fit(job("Backend Engineer", "Remote - India")).ok
    assert fit(job("Backend Engineer", "Remote")).ok
    assert fit(job("Backend Engineer", "Mumbai")).ok
    assert fit(job("Backend Engineer", "")).ok                                   # unknown location: not a reason to skip
    for vague in ("Hybrid", "In-Office", "4 Locations", "On-site"):               # says nothing about the country: let it through
        assert fit(job("Backend Engineer", vague)).ok, vague
    for place in ("Remote - US", "Remote, Canada", "London, United Kingdom", "Remote, Poland; Remote, United Kingdom", "Toronto",
                  "San Francisco, CA", "Westminster, CO", "Israel, Yokneam", "Belo Horizonte, MG, br", "Remote - Americas"):
        assert not fit(job("Backend Engineer", place)).ok, place
    assert fit(job("Backend Engineer", "Remote, United States or India")).ok      # India is allowed among others


def test_profile_filters_override_the_defaults():
    filters = {"exclude_titles": ["devops"], "locations": ["pune"]}
    assert not fit(job("DevOps Engineer", "Pune"), filters).ok
    assert fit(job("Backend Engineer", "Pune"), filters).ok
    assert not fit(job("Backend Engineer", "Mumbai"), filters).ok
    assert isinstance(fit(job("Backend Engineer")), FitDecision)


def test_vague_locations_ask_for_a_page_check_and_clear_ones_do_not():
    assert fit(job("Backend Engineer", "Hybrid")).needs_page_check
    assert fit(job("Backend Engineer", "")).needs_page_check
    assert fit(job("Backend Engineer", "Bengaluru, India")).needs_page_check is False
    assert fit(job("Backend Engineer", "Remote")).needs_page_check is False
