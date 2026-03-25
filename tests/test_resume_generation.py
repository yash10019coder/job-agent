import subprocess
import json
from pathlib import Path
from uuid import uuid4

from job_agent.inventory import load_resume_inventory
from job_agent.latex import build_resume_variant, pdf_page_count
from job_agent.models import Lane


def test_variants_keep_shared_facts() -> None:
    inventory = load_resume_inventory()
    cxx_variant = build_resume_variant(inventory, Lane.CXX)
    fullstack_variant = build_resume_variant(inventory, Lane.FULLSTACK_BACKEND)

    cxx_roles = {role.company: role for role in cxx_variant.roles}
    fullstack_roles = {role.company: role for role in fullstack_variant.roles}

    assert cxx_roles["Samsung R&D"].start == fullstack_roles["Samsung R&D"].start == "Jul 2024"
    assert cxx_roles["Samsung R&D"].end == fullstack_roles["Samsung R&D"].end == "Present"
    assert cxx_variant.education.end == fullstack_variant.education.end == "2024"


def test_cxx_variant_prioritizes_systems_bullets() -> None:
    inventory = load_resume_inventory()
    cxx_variant = build_resume_variant(inventory, Lane.CXX)
    samsung_bullets = [bullet.id for role in cxx_variant.roles if role.company == "Samsung R&D" for bullet in role.bullets]
    assert "samsung-dbus-concurrency" in samsung_bullets
    assert "samsung-remote-management" in samsung_bullets


def test_resume_compiles_to_single_page() -> None:
    output_dir = Path("outputs") / "test_resume_generation" / uuid4().hex
    output_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [str(Path(".venv") / "Scripts" / "job-agent.exe"), "generate-resumes", "--output-dir", str(output_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    tex_path = Path(payload["fullstack_backend"]["tex"])
    assert tex_path.exists()
    pdf_value = payload["fullstack_backend"]["pdf"]
    if pdf_value:
        assert pdf_page_count(Path(pdf_value)) == 1
