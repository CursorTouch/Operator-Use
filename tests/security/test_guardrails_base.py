"""Unit tests for operator_use/guardrails/ base classes and concrete helpers."""

import pytest

from operator_use.guardrails.base import (
    ActionValidator,
    ContentFilter,
    GuardrailAction,
    GuardrailResult,
    PolicyEngine,
    RiskLevel,
)
from operator_use.guardrails.action_validator import (
    AllowAllValidator,
    BlockListValidator,
    CompositeActionValidator,
)
from operator_use.guardrails.content_filter import (
    CompositeContentFilter,
    KeywordBlockFilter,
    PassthroughFilter,
    RegexFilter,
)
from operator_use.guardrails.policy_engine import (
    CompositePolicyEngine,
    RuleBasedPolicyEngine,
)
from operator_use.guardrails.registry import GuardrailRegistry


# ---------------------------------------------------------------------------
# GuardrailResult
# ---------------------------------------------------------------------------


class TestGuardrailResult:
    def test_allow_factory(self):
        r = GuardrailResult.allow("OK")
        assert r.action == GuardrailAction.ALLOW
        assert r.is_allowed
        assert not r.is_blocked
        assert not r.needs_confirmation
        assert r.severity == RiskLevel.SAFE

    def test_block_factory(self):
        r = GuardrailResult.block("dangerous")
        assert r.action == GuardrailAction.BLOCK
        assert r.is_blocked
        assert r.severity == RiskLevel.DANGEROUS

    def test_confirm_factory(self):
        r = GuardrailResult.confirm("needs review")
        assert r.action == GuardrailAction.CONFIRM
        assert r.needs_confirmation
        assert r.severity == RiskLevel.REVIEW

    def test_metadata_defaults_to_empty_dict(self):
        r = GuardrailResult.allow()
        assert r.metadata == {}

    def test_custom_severity_on_allow(self):
        r = GuardrailResult.allow("ok", severity=RiskLevel.REVIEW)
        assert r.severity == RiskLevel.REVIEW


# ---------------------------------------------------------------------------
# ActionValidator — abstract interface enforced
# ---------------------------------------------------------------------------


class TestActionValidatorAbstract:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            ActionValidator(name="test")  # type: ignore[abstract]

    def test_concrete_subclass_requires_validate(self):
        class Broken(ActionValidator):
            pass  # missing validate

        with pytest.raises(TypeError):
            Broken(name="broken")  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# ContentFilter — abstract interface enforced
# ---------------------------------------------------------------------------


class TestContentFilterAbstract:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            ContentFilter(name="test")  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# PolicyEngine — abstract interface enforced
# ---------------------------------------------------------------------------


class TestPolicyEngineAbstract:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            PolicyEngine(name="test")  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# AllowAllValidator
# ---------------------------------------------------------------------------


class TestAllowAllValidator:
    def setup_method(self):
        self.v = AllowAllValidator()

    def test_allows_any_tool(self):
        for tool in ("shell", "delete_file", "read_file", ""):
            r = self.v.validate(tool, {}, {})
            assert r.is_allowed, f"Expected allowed for {tool!r}"

    def test_check_delegates_to_validate(self):
        r = self.v.check({"tool_name": "shell", "args": {}})
        assert r.is_allowed

    def test_enabled_by_default(self):
        assert self.v.enabled is True


# ---------------------------------------------------------------------------
# BlockListValidator
# ---------------------------------------------------------------------------


class TestBlockListValidator:
    def setup_method(self):
        self.v = BlockListValidator(blocked_tools={"shell", "delete_file"})

    def test_blocks_listed_tool(self):
        r = self.v.validate("shell", {}, {})
        assert r.is_blocked
        assert r.severity == RiskLevel.DANGEROUS

    def test_allows_unlisted_tool(self):
        r = self.v.validate("read_file", {}, {})
        assert r.is_allowed

    def test_empty_blocklist_allows_all(self):
        v = BlockListValidator()
        assert v.validate("shell", {}, {}).is_allowed

    def test_check_delegates(self):
        r = self.v.check({"tool_name": "delete_file", "args": {}})
        assert r.is_blocked


# ---------------------------------------------------------------------------
# CompositeActionValidator
# ---------------------------------------------------------------------------


class TestCompositeActionValidator:
    def test_empty_composite_allows(self):
        c = CompositeActionValidator()
        r = c.validate("anything", {}, {})
        assert r.is_allowed

    def test_block_short_circuits(self):
        c = CompositeActionValidator(
            validators=[
                BlockListValidator(blocked_tools={"shell"}),
                AllowAllValidator(),
            ]
        )
        r = c.validate("shell", {}, {})
        assert r.is_blocked

    def test_confirm_preserved_when_no_block(self):
        class ConfirmAll(ActionValidator):
            def __init__(self):
                super().__init__(name="confirm_all")

            def validate(self, tool_name, args, context):
                return GuardrailResult.confirm("needs review")

        c = CompositeActionValidator(validators=[ConfirmAll(), AllowAllValidator()])
        r = c.validate("anything", {}, {})
        assert r.needs_confirmation

    def test_disabled_validator_skipped(self):
        blocker = BlockListValidator(blocked_tools={"shell"})
        blocker.enabled = False
        c = CompositeActionValidator(validators=[blocker])
        assert c.validate("shell", {}, {}).is_allowed

    def test_add_validator(self):
        c = CompositeActionValidator()
        c.add(BlockListValidator(blocked_tools={"shell"}))
        assert c.validate("shell", {}, {}).is_blocked


# ---------------------------------------------------------------------------
# PassthroughFilter
# ---------------------------------------------------------------------------


class TestPassthroughFilter:
    def test_passes_any_content(self):
        f = PassthroughFilter()
        for content in ("hello", "", "DROP TABLE users;", "rm -rf /"):
            assert f.filter(content, {}).is_allowed


# ---------------------------------------------------------------------------
# KeywordBlockFilter
# ---------------------------------------------------------------------------


class TestKeywordBlockFilter:
    def setup_method(self):
        self.f = KeywordBlockFilter(blocked_phrases={"drop table", "rm -rf"})

    def test_blocks_matching_phrase(self):
        r = self.f.filter("please run rm -rf /", {})
        assert r.is_blocked
        assert r.severity == RiskLevel.DANGEROUS

    def test_case_insensitive_by_default(self):
        r = self.f.filter("DROP TABLE users;", {})
        assert r.is_blocked

    def test_allows_clean_content(self):
        r = self.f.filter("list all files", {})
        assert r.is_allowed

    def test_case_sensitive_mode(self):
        f = KeywordBlockFilter(blocked_phrases={"DROP TABLE"}, case_sensitive=True)
        assert f.filter("DROP TABLE users;", {}).is_blocked
        assert f.filter("drop table users;", {}).is_allowed


# ---------------------------------------------------------------------------
# RegexFilter
# ---------------------------------------------------------------------------


class TestRegexFilter:
    def test_blocks_matching_pattern(self):
        f = RegexFilter(patterns=[r"\bpassword\s*=\s*\S+"])
        r = f.filter("password = hunter2", {})
        assert r.is_blocked

    def test_allows_non_matching(self):
        f = RegexFilter(patterns=[r"\bpassword\s*=\s*\S+"])
        r = f.filter("no secrets here", {})
        assert r.is_allowed

    def test_empty_patterns_allows_all(self):
        f = RegexFilter()
        assert f.filter("anything", {}).is_allowed


# ---------------------------------------------------------------------------
# CompositeContentFilter
# ---------------------------------------------------------------------------


class TestCompositeContentFilter:
    def test_empty_composite_allows(self):
        c = CompositeContentFilter()
        assert c.filter("anything", {}).is_allowed

    def test_block_short_circuits(self):
        c = CompositeContentFilter(
            filters=[
                KeywordBlockFilter(blocked_phrases={"bad"}),
                PassthroughFilter(),
            ]
        )
        r = c.filter("bad content", {})
        assert r.is_blocked

    def test_disabled_filter_skipped(self):
        blocker = KeywordBlockFilter(blocked_phrases={"bad"})
        blocker.enabled = False
        c = CompositeContentFilter(filters=[blocker])
        assert c.filter("bad content", {}).is_allowed

    def test_add_filter(self):
        c = CompositeContentFilter()
        c.add(KeywordBlockFilter(blocked_phrases={"bad"}))
        assert c.filter("bad content", {}).is_blocked


# ---------------------------------------------------------------------------
# RuleBasedPolicyEngine
# ---------------------------------------------------------------------------


class TestRuleBasedPolicyEngine:
    def setup_method(self):
        self.engine = RuleBasedPolicyEngine(
            dangerous_tools={"shell", "delete_file"},
            review_tools={"write_file"},
        )

    def test_classify_dangerous(self):
        assert self.engine.classify_risk({"tool_name": "shell"}) == RiskLevel.DANGEROUS

    def test_classify_review(self):
        assert self.engine.classify_risk({"tool_name": "write_file"}) == RiskLevel.REVIEW

    def test_classify_safe(self):
        assert self.engine.classify_risk({"tool_name": "read_file"}) == RiskLevel.SAFE

    def test_check_blocks_dangerous(self):
        r = self.engine.check({"tool_name": "shell"})
        assert r.is_blocked

    def test_check_confirms_review(self):
        r = self.engine.check({"tool_name": "write_file"})
        assert r.needs_confirmation

    def test_check_allows_safe(self):
        r = self.engine.check({"tool_name": "read_file"})
        assert r.is_allowed

    def test_unknown_tool_is_safe(self):
        assert self.engine.classify_risk({"tool_name": ""}) == RiskLevel.SAFE


# ---------------------------------------------------------------------------
# CompositePolicyEngine
# ---------------------------------------------------------------------------


class TestCompositePolicyEngine:
    def test_empty_composite_is_safe(self):
        c = CompositePolicyEngine()
        assert c.classify_risk({}) == RiskLevel.SAFE

    def test_dangerous_wins_over_review(self):
        e1 = RuleBasedPolicyEngine(review_tools={"write_file"})
        e2 = RuleBasedPolicyEngine(dangerous_tools={"write_file"})
        c = CompositePolicyEngine(engines=[e1, e2])
        assert c.classify_risk({"tool_name": "write_file"}) == RiskLevel.DANGEROUS

    def test_review_preserved_when_no_dangerous(self):
        e1 = RuleBasedPolicyEngine(review_tools={"write_file"})
        e2 = RuleBasedPolicyEngine()
        c = CompositePolicyEngine(engines=[e1, e2])
        assert c.classify_risk({"tool_name": "write_file"}) == RiskLevel.REVIEW

    def test_disabled_engine_skipped(self):
        e = RuleBasedPolicyEngine(dangerous_tools={"shell"})
        e.enabled = False
        c = CompositePolicyEngine(engines=[e])
        assert c.classify_risk({"tool_name": "shell"}) == RiskLevel.SAFE

    def test_add_engine(self):
        c = CompositePolicyEngine()
        c.add(RuleBasedPolicyEngine(dangerous_tools={"shell"}))
        assert c.classify_risk({"tool_name": "shell"}) == RiskLevel.DANGEROUS


# ---------------------------------------------------------------------------
# GuardrailRegistry
# ---------------------------------------------------------------------------


class TestGuardrailRegistry:
    def setup_method(self):
        self.registry = GuardrailRegistry()

    def test_register_and_get(self):
        v = AllowAllValidator()
        self.registry.register(v)
        assert self.registry.get("allow_all") is v

    def test_get_unknown_returns_none(self):
        assert self.registry.get("nonexistent") is None

    def test_register_replaces_same_name(self):
        v1 = AllowAllValidator()
        v2 = AllowAllValidator()
        self.registry.register(v1)
        self.registry.register(v2)
        assert self.registry.get("allow_all") is v2

    def test_unregister(self):
        self.registry.register(AllowAllValidator())
        self.registry.unregister("allow_all")
        assert self.registry.get("allow_all") is None

    def test_unregister_missing_is_noop(self):
        self.registry.unregister("does_not_exist")  # should not raise

    def test_get_all_returns_all(self):
        self.registry.register(AllowAllValidator())
        self.registry.register(PassthroughFilter())
        assert len(self.registry.get_all()) == 2

    def test_get_all_filtered_by_type(self):
        self.registry.register(AllowAllValidator())
        self.registry.register(PassthroughFilter())
        validators = self.registry.get_all(ActionValidator)
        assert len(validators) == 1
        assert isinstance(validators[0], ActionValidator)

    def test_get_enabled_excludes_disabled(self):
        v = AllowAllValidator()
        v.enabled = False
        self.registry.register(v)
        assert self.registry.get_enabled(ActionValidator) == []

    def test_run_all_returns_results(self):
        self.registry.register(AllowAllValidator())
        results = self.registry.run_all(ActionValidator, context={"tool_name": "x", "args": {}})
        assert len(results) == 1
        assert results[0].is_allowed

    def test_clear(self):
        self.registry.register(AllowAllValidator())
        self.registry.clear()
        assert len(self.registry) == 0

    def test_len(self):
        self.registry.register(AllowAllValidator())
        self.registry.register(PassthroughFilter())
        assert len(self.registry) == 2

    def test_repr(self):
        self.registry.register(AllowAllValidator())
        assert "allow_all" in repr(self.registry)
