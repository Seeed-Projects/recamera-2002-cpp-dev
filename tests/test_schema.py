import json

from demo_lib import validate_schema


def test_minimal_object_passes():
    schema = {
        "type": "object",
        "required": ["name", "count"],
        "properties": {
            "name": {"type": "string", "pattern": "^[a-z]+$"},
            "count": {"type": "integer", "minimum": 1},
        },
    }
    assert validate_schema({"name": "demo", "count": 2}, schema) == []


def test_type_and_required_errors():
    schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
    errors = validate_schema({}, schema)
    assert any("missing required property 'name'" in error for error in errors)
    errors = validate_schema({"name": 3}, schema)
    assert any("expected string, got integer" in error for error in errors)


def test_bool_is_not_integer():
    schema = {"type": "integer"}
    assert validate_schema(True, schema)


def test_enum_and_pattern():
    schema = {"type": "string", "enum": ["cpp", "cmake"], "pattern": "^c"}
    assert validate_schema("cpp", schema) == []
    assert validate_schema("rust", schema)


def test_additional_properties_false():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "additionalProperties": False}
    assert validate_schema({"a": 1}, schema) == []
    assert validate_schema({"a": 1, "b": 2}, schema)


def test_items_and_bounds():
    schema = {"type": "array", "items": {"type": "number"}, "minItems": 1, "maxItems": 2}
    assert validate_schema([1], schema) == []
    assert validate_schema([], schema)
    assert validate_schema([1, 2, 3], schema)
    assert validate_schema([1, "x"], schema)


def test_any_of():
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert validate_schema("x", schema) == []
    assert validate_schema(1, schema) == []
    assert validate_schema([], schema)


def test_demo_schema_accepts_bundled_example(skill_root):
    schema = json.loads((skill_root / "schemas/demo.schema.json").read_text())
    example = json.loads((skill_root / "examples/hello_cpp/demo.json").read_text())
    assert validate_schema(example, schema) == []


def test_demo_schema_rejects_bad_name(skill_root):
    schema = json.loads((skill_root / "schemas/demo.schema.json").read_text())
    example = json.loads((skill_root / "examples/hello_cpp/demo.json").read_text())
    example["name"] = "Bad Name"
    errors = validate_schema(example, schema)
    assert any("name" in error for error in errors)