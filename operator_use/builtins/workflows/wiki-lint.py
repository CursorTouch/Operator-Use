meta = {
    "name": "wiki-lint",
    "description": "Check wiki pages for contradictions, stale claims, and broken cross-links.",
    "when_to_use": "User runs /wiki lint.",
    "phases": [
        {"name": "scan",   "description": "Read all wiki pages"},
        {"name": "report", "description": "Identify and report issues"},
    ],
}


async def run():
    knowledge_dir = args.get("knowledge_dir", "")

    async with phase("scan"):
        log("Reading all wiki pages...")
        pages_summary = await agent(
            f"List every .md file in '{knowledge_dir}' and return a brief (1-2 sentence) "
            "summary of each page's topic.",
            tools=["read"],
        )

    async with phase("report"):
        log("Checking for issues...")
        report = await agent(
            f"You are auditing the wiki at '{knowledge_dir}'.\n\n"
            f"Pages overview:\n{pages_summary}\n\n"
            "Read all pages thoroughly, then produce a structured lint report with these sections:\n"
            "1. **Contradictions** — claims on different pages that conflict with each other.\n"
            "2. **Stale content** — claims that appear outdated or superseded by newer pages.\n"
            "3. **Broken cross-links** — references to pages or anchors that don't exist.\n"
            "4. **Near-duplicate pages** — pages with substantial content overlap.\n\n"
            "For each finding include: page(s) affected, the specific issue, and a suggested fix. "
            "If no issues are found in a category, say 'None found.'",
            tools=["read"],
        )

    return report
