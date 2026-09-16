"""
AGENT LAYER — TOOL ADAPTER
-----------------------------
Converts the EXISTING, unmodified Tool Registry's tool specs into the
input_schema format Claude's tool-use API expects. This is the only place
that knows about that format - the Tool Registry itself stays
provider-independent.

Only tools present in BOTH the registry AND the caller-supplied
`allowed_tools` set are ever exposed to the model. There is no "expose
everything by default" path.
"""

from providers import ToolDefinition

# Hand-written JSON schemas for each tool's arguments. The registry's
# ToolSpec only tracks *names* of required args (enough for the registry's
# own validation) - the model needs full JSON Schema types/descriptions to
# make good tool calls, so that's maintained here, one level up.
_TOOL_INPUT_SCHEMAS: dict[str, dict] = {
    "read_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the project root."},
            "max_bytes": {"type": "integer", "description": "Max bytes to read.", "default": 200000},
        },
        "required": ["path"],
    },
    "write_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the project root."},
            "content": {"type": "string", "description": "Full file content to write."},
            "overwrite": {"type": "boolean", "description": "Overwrite if the file already exists.", "default": True},
        },
        "required": ["path", "content"],
    },
    "edit_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_str": {"type": "string", "description": "Exact text to replace (must be unique in the file)."},
            "new_str": {"type": "string", "description": "Replacement text."},
        },
        "required": ["path", "old_str", "new_str"],
    },
    "list_files": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "default": "."},
            "recursive": {"type": "boolean", "default": False},
        },
        "required": [],
    },
    "search_files": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for."},
            "path": {"type": "string", "default": "."},
            "max_results": {"type": "integer", "default": 100},
        },
        "required": ["pattern"],
    },
    "create_directory": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    "create_project": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "files": {"type": "object", "description": "Map of relative file path -> file content.", "additionalProperties": {"type": "string"}},
        },
        "required": ["path", "files"],
    },
    "run_command": {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string", "default": "."},
            "timeout": {"type": "integer", "default": 30},
        },
        "required": ["command"],
    },
    "run_tests": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "default": "."},
            "test_command": {"type": "string", "description": "Override test command; defaults to python3 -m unittest discover."},
            "timeout": {"type": "integer", "default": 60},
        },
        "required": [],
    },
    "inspect_project": {
        "type": "object",
        "properties": {"path": {"type": "string", "default": "."}},
        "required": [],
    },
}


def build_tool_definitions(tool_registry, allowed_tools: set) -> list[ToolDefinition]:
    """tool_registry: an existing tools.registry.ToolRegistry instance.
    allowed_tools: names permitted for THIS task - not every registered tool."""
    available = set(tool_registry.available_tools())
    exposed = sorted(available & set(allowed_tools))

    defs = []
    for name in exposed:
        spec = tool_registry._specs[name]  # read-only access to an existing spec
        schema = _TOOL_INPUT_SCHEMAS.get(name, {
            "type": "object",
            "properties": {arg: {"type": "string"} for arg in spec.required_args},
            "required": sorted(spec.required_args),
        })
        defs.append(ToolDefinition(name=name, description=spec.description, input_schema=schema))
    return defs
