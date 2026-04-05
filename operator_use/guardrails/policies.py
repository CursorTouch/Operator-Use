from .base import ActionPolicy, RiskLevel


class DefaultPolicy(ActionPolicy):
    """Default risk classification for all built-in tools.

    The browser tool uses a single name "browser" with an "action" arg to
    multiplex all operations. High-risk actions (script, download) are classified
    DANGEROUS; navigation/inspection actions are REVIEW.
    """

    name = "default"

    DANGEROUS_TOOLS = {"terminal", "write_file", "edit_file"}

    # Browser actions that are always dangerous regardless of context
    BROWSER_DANGEROUS_ACTIONS = {"script", "download"}
    # Browser actions that warrant review (navigation, interaction)
    BROWSER_REVIEW_ACTIONS = {
        "goto", "click", "type", "scroll", "screenshot",
        "tab", "wait", "select", "upload", "menu",
    }

    REVIEW_TOOLS = {"read_file", "list_dir"}

    def assess(self, tool_name: str, args: dict) -> RiskLevel:
        if tool_name in self.DANGEROUS_TOOLS:
            return RiskLevel.DANGEROUS

        if tool_name == "browser":
            action = args.get("action", "")
            if action in self.BROWSER_DANGEROUS_ACTIONS:
                return RiskLevel.DANGEROUS
            if action in self.BROWSER_REVIEW_ACTIONS:
                return RiskLevel.REVIEW
            # Unknown browser action — treat as review to err on the safe side
            return RiskLevel.REVIEW

        if tool_name in self.REVIEW_TOOLS:
            return RiskLevel.REVIEW

        return RiskLevel.SAFE
