meta = {
    "name": "wiki-dream",
    "description": "Consolidate the wiki — deduplicate, merge near-identical pages, remove stale content.",
    "when_to_use": "User runs /wiki dream.",
    "phases": [
        {"name": "read",        "description": "Read all wiki pages"},
        {"name": "consolidate", "description": "Merge, deduplicate, clean"},
        {"name": "index",       "description": "Rebuild index.yaml and log.md"},
    ],
}


async def run():
    knowledge_dir = args.get("knowledge_dir", "")

    async with phase("read"):
        log("Reading wiki pages...")
        overview = await agent(
            f"List every .md file in '{knowledge_dir}' and return a table with: "
            "filename | topic summary (1 sentence) | approximate word count.",
            tools=["read"],
        )

    async with phase("consolidate"):
        log("Consolidating wiki...")
        await agent(
            f"You are consolidating the wiki at '{knowledge_dir}'.\n\n"
            f"Current pages:\n{overview}\n\n"
            "Read all pages, then apply these consolidation rules:\n"
            "1. **Merge near-duplicates** — if two pages cover the same concept, merge them "
            "into the more complete one and delete the other.\n"
            "2. **Deduplicate within pages** — remove repeated facts stated multiple times; "
            "keep the most accurate and complete statement.\n"
            "3. **Remove stale content** — delete claims contradicted by other pages or "
            "clearly outdated. When in doubt, keep content.\n"
            "4. **Tighten cross-links** — fix broken references, add missing links between "
            "related pages, remove links to deleted pages.\n"
            "5. Write all changed files to disk. Delete merged-away files.\n\n"
            "Make minimal changes — preserve the existing structure and voice.",
            tools=["read", "write"],
        )

    async with phase("index"):
        log("Rebuilding index.yaml and updating log.md...")
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        await agent(
            f"Rebuild the wiki index and audit log in '{knowledge_dir}':\n\n"
            "1. Read the current '{knowledge_dir}/index.yaml'.\n"
            "2. List all .md files now present in the directory.\n"
            "3. Rewrite index.yaml so it exactly matches the current files "
            "(add missing entries, remove deleted ones, preserve existing settings).\n"
            "4. Append to '{knowledge_dir}/log.md':\n"
            f"   - {timestamp} | dream | consolidation pass completed\n\n"
            "Write both files.",
            tools=["read", "write"],
        )

    return f"Wiki dream complete at '{knowledge_dir}'."
