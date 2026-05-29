"""Prompt templates for knowledge workflow agents."""
from __future__ import annotations

# ── Shared structure rules ────────────────────────────────────────────────────

STRUCTURE_RULES = """\
STRUCTURE RULES — follow these exactly:
- Each topic gets its own folder: '{knowledge_dir}/{topic-slug}/'
- The folder must contain an 'index.md' as the main entry point
- Sub-topics get their own .md files inside the same folder
- index.md links to sub-pages using relative markdown links: [Sub Topic](./sub-topic.md)
- Sub-pages link back to index.md and cross-link to related pages
- Use lowercase kebab-case for all folder and file names

Example structure for a topic 'self-improving-agents':
  {knowledge_dir}/self-improving-agents/index.md       ← overview + links
  {knowledge_dir}/self-improving-agents/loop-pattern.md
  {knowledge_dir}/self-improving-agents/memory-channels.md\
"""

# ── Ingest ────────────────────────────────────────────────────────────────────

def ingest_read(source: str) -> str:
    return (
        f"Read the file at '{source}' and return its full content verbatim. "
        "Do not summarize."
    )


def ingest_synthesize(knowledge_dir: str, source_content: str) -> str:
    return (
        f"You are maintaining a knowledge base in '{knowledge_dir}'.\n\n"
        f"{STRUCTURE_RULES}\n\n"
        "Steps:\n"
        "1. Decide on a kebab-case topic slug from the source content.\n"
        "2. Check if a folder for this topic already exists — if so, update it.\n"
        "3. Break the content into logical sub-topics, one .md file each.\n"
        "4. Write index.md first with a summary and links to sub-pages.\n"
        "5. Write each sub-page with full content and back-links.\n"
        "6. Do NOT create flat .md files in the knowledge root.\n\n"
        f"Source content:\n{source_content}"
    )


def ingest_index(knowledge_dir: str, source: str, timestamp: str) -> str:
    return (
        f"Update the knowledge index and audit log in '{knowledge_dir}':\n\n"
        "1. List the directories now present in the knowledge folder.\n"
        f"2. Read '{knowledge_dir}/index.yaml' (create it if missing).\n"
        "3. For each topic folder that has an index.md, add an entry if not present:\n"
        "     - path: {topic-slug}/index.md\n"
        "       always_load: false\n"
        "       priority: normal\n"
        "       tags: [{relevant, tags}]\n"
        "   Preserve existing entries.\n"
        f"4. Append to '{knowledge_dir}/log.md':\n"
        f"   - {timestamp} | ingest | source: {source}\n"
        "5. Write both files."
    )


# ── Lint ──────────────────────────────────────────────────────────────────────

def lint_scan(knowledge_dir: str) -> str:
    return (
        f"List every .md file in '{knowledge_dir}' and return a one-sentence "
        "summary of each page's topic."
    )


def lint_report(knowledge_dir: str, overview: str) -> str:
    return (
        f"Audit the knowledge base at '{knowledge_dir}'.\n\n"
        f"Pages overview:\n{overview}\n\n"
        "Read all pages, then produce a structured report with these sections:\n"
        "1. **Contradictions** — claims on different pages that conflict.\n"
        "2. **Stale content** — claims that appear outdated or superseded.\n"
        "3. **Broken cross-links** — references to pages or anchors that don't exist.\n"
        "4. **Near-duplicate pages** — pages with substantial content overlap.\n\n"
        "For each finding include: page(s) affected, the issue, and a suggested fix. "
        "If no issues in a category, say 'None found.'"
    )


# ── Dream ─────────────────────────────────────────────────────────────────────

def dream_read(knowledge_dir: str) -> str:
    return (
        f"List every .md file in '{knowledge_dir}' with: "
        "filename | one-sentence topic summary | approximate word count."
    )


def dream_consolidate(knowledge_dir: str, overview: str) -> str:
    return (
        f"Consolidate the knowledge base at '{knowledge_dir}'.\n\n"
        f"Current pages:\n{overview}\n\n"
        "STRUCTURE RULES — enforce during consolidation:\n"
        "- Each topic must live in its own folder: '{topic-slug}/index.md' + sub-pages\n"
        "- Flat .md files in the knowledge root (except index.yaml and log.md) "
        "should be moved into an appropriate topic folder\n"
        "- Folder and file names must be lowercase kebab-case\n\n"
        "Consolidation rules:\n"
        "1. **Restructure flat files** — move any root-level .md files into a "
        "topic folder with an index.md.\n"
        "2. **Merge near-duplicates** — merge pages covering the same concept.\n"
        "3. **Deduplicate within pages** — remove repeated facts.\n"
        "4. **Remove stale content** — delete claims contradicted by newer pages.\n"
        "5. **Tighten cross-links** — fix broken references, update links after moves.\n"
        "6. Write all changed files. Delete merged-away files.\n\n"
        "Make minimal changes — preserve the existing structure and voice."
    )


def dream_index(knowledge_dir: str, timestamp: str) -> str:
    return (
        f"Rebuild the knowledge index and audit log in '{knowledge_dir}':\n\n"
        "1. List all topic folders (subdirectories) now present.\n"
        f"2. Read '{knowledge_dir}/index.yaml'.\n"
        "3. Rewrite index.yaml so each entry points to a folder's index.md:\n"
        "     - path: {topic-slug}/index.md\n"
        "       always_load: false\n"
        "       priority: normal\n"
        "       tags: [{relevant, tags}]\n"
        "   Remove entries for deleted folders. Preserve always_load and priority "
        "settings for existing entries.\n"
        f"4. Append to '{knowledge_dir}/log.md':\n"
        f"   - {timestamp} | dream | consolidation pass completed\n"
        "5. Write both files."
    )


# ── Query ─────────────────────────────────────────────────────────────────────

def query_answer(knowledge_dir: str, question: str) -> str:
    return (
        f"Answer the following question using only the knowledge base at '{knowledge_dir}'.\n"
        "Steps:\n"
        "1. List the pages in the knowledge directory.\n"
        "2. Read the pages most likely to contain the answer.\n"
        "3. Answer the question based solely on what those pages say.\n"
        "4. Cite which page each part of your answer comes from.\n"
        "5. If the knowledge base does not contain enough information, say so explicitly.\n\n"
        f"Question: {question}"
    )
