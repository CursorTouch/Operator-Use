from datetime import datetime, timedelta
from pathlib import Path


class Memory:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.memory_dir = workspace / "memory"
        self.memory_file = self.memory_dir / "MEMORY.md"

    def _daily_log_path(self, date: str) -> Path:
        return self.memory_dir / f"{date}.md"

    def read_memory(self) -> str:
        if not self.memory_file.exists():
            return ""
        return self.memory_file.read_text(encoding="utf-8")

    def write_memory(self, memory: str) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file.write_text(memory, encoding="utf-8")

    def read_daily_log(self, date: str) -> str:
        path = self._daily_log_path(date)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def get_memory_context(self) -> str | None:
        sections: list[str] = []

        long_term = self.read_memory()
        if long_term:
            sections.append(f"### Long-Term Memory\n\n{long_term}")

        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        for date, label in [(today, "Today"), (yesterday, "Yesterday")]:
            log = self.read_daily_log(date)
            if log:
                sections.append(f"### {label}'s Log ({date})\n\n{log}")

        if not sections:
            return None
        return "## Memory\n\n" + "\n\n---\n\n".join(sections)
