import json

from app.agent.orchestrator import _submit_report_tool
from app.agent.schemas import FinalReport
from app.tools.jsonschema import inline_refs


def test_inline_refs_removes_refs_and_defs():
    schema = FinalReport.model_json_schema()
    assert "$defs" in schema and '"$ref"' in json.dumps(schema)  # precondition: pydantic nests via $ref
    flat = inline_refs(schema)
    assert "$ref" not in json.dumps(flat) and "$defs" not in flat
    item = flat["properties"]["evidence"]["items"]
    assert set(item["properties"]) == {"tool", "finding"} and item["required"] == ["tool", "finding"]
    assert flat["properties"]["evidence"]["minItems"] == 1  # sibling keywords survive


def test_submit_report_tool_schema_is_flat_and_complete():
    tool = _submit_report_tool()
    assert "$ref" not in json.dumps(tool["input_schema"])
    assert set(tool["input_schema"]["required"]) == set(FinalReport.model_fields)


def test_schema_without_refs_is_unchanged():
    simple = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    assert inline_refs(simple) == simple
