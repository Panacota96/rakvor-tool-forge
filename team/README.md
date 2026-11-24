# Rakvor development team

A team of Claude Code agents that keeps improving the Rakvor suite. The orchestrator works through
`C:\Users\david\Rakvor\roadmap\ROADMAP.md` (private repo `Panacota96/rakvor-roadmap`) until it is empty or an
owner gate is reached.

```mermaid
flowchart TD
  O[rakvor-team skill: orchestrator] -->|next action| C{roadmap}
  C -->|open PR| PR[rv-code-reviewer + rv-auditor]
  C -->|todo item| IM[rv-implementer: branch + PR]
  C -->|nothing pending| RV[review rotation: doc, code, security, legal, tester, auditor]
  C -->|every 4th cycle| ID[rv-creative-researcher]
  ID --> PN[panel: rv-challenger, rv-security-reviewer, rv-legal-standards-reviewer]
  PN -->|2/3 approve, benefit > effort| IS[issue labelled proposal + roadmap item]
  PN -->|rejected| RJ[ideas/rejected with reasons]
  RV -->|findings| AU[rv-auditor confirms] --> C
  IM --> PR
  PR -->|CI green + 2 approvals, not a release| MG[merge]
  PR -->|changes requested, max 2| IM
  C -->|release, legal, owner-only| ST[stop: summary for the owner]
```

| Agent | Role | Writes |
| --- | --- | --- |
| `rv-doc-reviewer` | Docs, quick guides and CLI help match the code; EN/ES/FR | findings |
| `rv-code-reviewer` | Correctness, shared-file identity, tests; approves PRs | findings |
| `rv-security-reviewer` | SSDF, ASVS, SLSA, supply chain; idea panel | findings, scores |
| `rv-legal-standards-reviewer` | ISO 27037/41/42/43, 17025, NIST, CO/FR/US admissibility, licence; idea panel | findings, scores |
| `rv-tester` | QA harness, edge cases, large inputs | findings, synthetic data |
| `rv-auditor` | Independent re-check; approves PRs; confirms findings | findings |
| `rv-implementer` | The only one that writes code: a branch, checks and a PR | code, PRs |
| `rv-creative-researcher` | Feature research, full proposals | idea files |
| `rv-challenger` | Devil's advocate on the idea panel | scores |

## Install and run

```powershell
python -I scripts/install_team.py C:\Users\david\Rakvor
```

Then open a Claude Code session in `C:\Users\david\Rakvor`:

- `/rakvor-team` runs one cycle.
- `/loop /rakvor-team` keeps going until a stop condition.

## What the team may and may not do
- **May:**
  - open branches and PRs;
  - merge a non-release PR after green CI, two approvals (`rv-code-reviewer`, `rv-auditor`) and a clean Codex
    review;
  - create `proposal` issues for ideas the panel accepted;
  - commit to the roadmap repo.
- **May not:**
  - release, tag, push to `main` of a tool repo, force-push or delete branches;
  - remove legal draft markers;
  - add GPL code;
  - put secrets or personal data anywhere.

The rules every agent follows are in [RULES.md](RULES.md).
