meta = {
    "name": "wiki-ingest",
    "description": "Ingest a source document into the wiki knowledge base.",
    "when_to_use": "User runs /wiki ingest <source>.",
    "phases": [
        {"name": "read",       "description": "Read source document"},
        {"name": "synthesize", "description": "Update wiki pages"},
        {"name": "index",      "description": "Update index.yaml and log.md"},
    ],
}


async def run():
    source       = args.get("source", "")
    knowledge_dir = args.get("knowledge_dir", "")

    if not source:
        return "No source provided."

    async with phase("read"):
        log(f"Reading source: {source}")
        source_content = await agent(
            f"Read the file at '{source}' and return its full content verbatim. "
            "Do not summarize — return everything.",
            tools=["read"],
        )

    async with phase("synthesize"):
        log("Updating wiki pages...")
        await agent(
            f"You are maintaining a wiki in the directory '{knowledge_dir}'.\n\n"
            "Steps:\n"
            "1. Use the read tool to list all existing .md files in the wiki directory.\n"
            "2. Read each existing page to understand current coverage.\n"
            "3. Decide which pages to update and whether any new page is needed.\n"
            "4. Update or create pages to incorporate the knowledge from the source below.\n"
            "5. Add cross-links between related pages where relevant.\n"
            "6. Write all changes back to disk.\n\n"
            f"Source content:\n{source_content}",
            tools=["read", "write"],
        )

    async with phase("index"):
        log("Updating index.yaml and log.md...")
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        await agent(
            f"Update the wiki index and audit log in '{knowledge_dir}':\n\n"
            "1. Read '{knowledge_dir}/index.yaml'. Add any new pages created in this "
            "ingest run (always_load: false, priority: normal). Preserve existing entries.\n"
            "2. Append a new line to '{knowledge_dir}/log.md' in the format:\n"
            f"   - {timestamp} | ingest | source: {source} | pages updated: <list>\n\n"
            "Write both files back to disk.",
            tools=["read", "write"],
        )

    return f"Ingested '{source}' into wiki at '{knowledge_dir}'."
