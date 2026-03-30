from .base import ActionPolicy, RiskLevel


class DefaultPolicy(ActionPolicy):
    """Default risk classification for all built-in tools."""

    name = "default"

    DANGEROUS_TOOLS = {"terminal", "write_file", "edit_file", "browser_script", "download"}
    REVIEW_TOOLS = {"read_file", "list_dir", "browser_navigate", "browser_screenshot"}

    def assess(self, tool_name: str, args: dict) -> RiskLevel:
        if tool_name in self.DANGEROUS_TOOLS:
            return RiskLevel.DANGEROUS
        if tool_name in self.REVIEW_TOOLS:
            return RiskLevel.REVIEW
        return RiskLevel.SAFE
