"""Install the Rakvor development team into a workspace's .claude folder.

usage: python -I scripts/install_team.py [WORKSPACE]   (default: the folder above this repository)

Copies team/agents/*.md to <WORKSPACE>/.claude/agents/, team/skills/* to <WORKSPACE>/.claude/skills/,
and team/RULES.md to <WORKSPACE>/.claude/rakvor-team/RULES.md. Existing files with the same name are
replaced; nothing else is touched. Prints what it copied with each file's SHA-256 prefix.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEAM = REPO / "team"


def copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    digest = hashlib.sha256(dst.read_bytes()).hexdigest()[:12]
    print(f"{digest}  {dst}")


def main(argv: list[str]) -> int:
    workspace = Path(argv[1]).resolve() if len(argv) > 1 else REPO.parent
    if not TEAM.is_dir():
        print(f"no team folder at {TEAM}", file=sys.stderr)
        return 2
    claude = workspace / ".claude"
    agents = sorted((TEAM / "agents").glob("*.md"))
    for agent in agents:
        copy(agent, claude / "agents" / agent.name)
    for skill in sorted((TEAM / "skills").iterdir()):
        if skill.is_dir():
            for f in sorted(skill.rglob("*")):
                if f.is_file():
                    copy(f, claude / "skills" / skill.name / f.relative_to(skill))
    copy(TEAM / "RULES.md", claude / "rakvor-team" / "RULES.md")
    print(f"installed {len(agents)} agents into {claude}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
