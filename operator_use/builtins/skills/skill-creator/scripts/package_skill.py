#!/usr/bin/env python3
"""
Package a skill directory into a .skill file for distribution.

Usage:
    python -m scripts.package_skill [skill_path] [output_path]

If no paths provided, packages the current directory.
"""

import json
import sys
import tarfile
import tempfile
from pathlib import Path

def package_skill(skill_path=None, output_path=None):
    """Package a skill directory into a .skill archive."""
    
    if not skill_path:
        skill_path = Path.cwd()
    else:
        skill_path = Path(skill_path)
    
    if not skill_path.exists():
        print(f"❌ Skill path not found: {skill_path}")
        sys.exit(1)
    
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        print(f"❌ SKILL.md not found in {skill_path}")
        sys.exit(1)
    
    # Extract skill name from SKILL.md
    with open(skill_md) as f:
        content = f.read()
        for line in content.split('\n'):
            if line.startswith("name:"):
                skill_name = line.split(":", 1)[1].strip()
                break
        else:
            skill_name = skill_path.name
    
    if not output_path:
        output_path = Path.cwd() / f"{skill_name}.skill"
    else:
        output_path = Path(output_path)
    
    # Create tar.gz archive
    try:
        with tarfile.open(output_path, "w:gz") as tar:
            tar.add(skill_path, arcname=skill_name)
        
        print(f"✅ Skill packaged successfully!")
        print(f"📦 Output: {output_path}")
        print(f"📏 Size: {output_path.stat().st_size / 1024:.1f} KB")
        
    except Exception as e:
        print(f"❌ Error packaging skill: {e}")
        sys.exit(1)

if __name__ == "__main__":
    skill_path = sys.argv[1] if len(sys.argv) > 1 else None
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    package_skill(skill_path, output_path)
