import subprocess
import json
from pathlib import Path
from uuid import uuid4

from job_agent.inventory import load_resume_inventory
from job_agent.latex import build_resume_variant, pdf_page_count
from job_agent.models import Lane


def test_fullstack_variant_generates() -> None:
    inventory = load_resume_inventory()
    fullstack_variant = build_resume_variant(inventory, Lane.FULLSTACK_BACKEND)

    fullstack_roles = {role.company: role for role in fullstack_variant.roles}

    assert fullstack_roles["Samsung R&D"].start == "Jul 2024"
    assert fullstack_roles["Samsung R&D"].end == "Present"
    assert fullstack_variant.education.end == "2024"


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
