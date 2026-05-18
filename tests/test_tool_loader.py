from pathlib import Path

from program.tool.loader import load_tool_from_file


def test_dynamic_tool_schema_with_postponed_annotations_builds_json_schema():
    tool_path = Path(__file__).parent.parent / 'program' / 'builtins' / 'tools' / 'cron.py'

    tools, errors = load_tool_from_file(tool_path)

    assert not errors
    cron_tool = next(tool for tool in tools if tool.name == 'cron')
    schema = cron_tool.schema.model_json_schema()

    assert schema['type'] == 'object'
    assert 'action' in schema['properties']
