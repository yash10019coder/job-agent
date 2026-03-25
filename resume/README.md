# Resume Authoring Notes

- `template.tex.j2` is the shared single-column LaTeX layout used by both resume lanes.
- `../data/resume_inventory.json` stores the factual inventory: roles, bullets, metrics, projects, achievements, and lane tags.
- Run `job-agent generate-resumes` to regenerate:
  - `outputs/resumes/resume_cxx.tex`
  - `outputs/resumes/resume_cxx.pdf`
  - `outputs/resumes/resume_fullstack_backend.tex`
  - `outputs/resumes/resume_fullstack_backend.pdf`

## Editing guidance

- Keep facts, dates, and metrics truthful in `resume_inventory.json`.
- Use `lane_tags` to decide whether a bullet should support `cxx`, `fullstack_backend`, or both.
- Prefer changing emphasis through bullet selection and ordering before rewriting copy.
- If Samsung is no longer officially current, update the `end` field in `resume_inventory.json` before generating resumes for real applications.
