"""Memnon connector: a remote MCP server at https://memnon.app/api/mcp.

Streamable HTTP in its stateless form: every POST carries one JSON-RPC
message (or a batch) and gets a JSON reply. No SSE stream and no session
id, so it runs inside the existing Flask Cloud Function. The tool shape
follows src/mcp_server.py's FastMCP pattern: a plain function registered
with @tool, whose docstring is its description.

Every tool is read-only. Phase 1 has one public tool, find_eap, and no
auth. Logs carry the tool name and uid only, never arguments or results.
"""
from __future__ import annotations

import json
from typing import Any, Callable

import eap_directory

SERVER_INFO = {"name": "memnon", "title": "Memnon", "version": "0.1.0"}
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "Memnon's public tools. find_eap looks up a Colorado school district's employee "
    "assistance program (EAP): provider, free sessions, phone and login, from the district's "
    "own pages. If the person may be in crisis, tell them to call or text 988 first."
)

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602

_TOOLS: dict[str, dict] = {}


def tool(title: str, input_schema: dict) -> Callable:
    """Register a read-only tool. The docstring's first paragraph is its description."""
    def register(fn: Callable) -> Callable:
        _TOOLS[fn.__name__] = {
            "fn": fn,
            "definition": {
                "name": fn.__name__,
                "title": title,
                "description": (fn.__doc__ or "").strip().split("\n\n")[0].replace("\n", " "),
                "inputSchema": input_schema,
                "annotations": {
                    "title": title,
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            },
        }
        return fn
    return register


@tool(
    "Find a Colorado school district's EAP",
    {
        "type": "object",
        "properties": {
            "district": {
                "type": "string",
                "description": "District name, short name or city, e.g. 'St. Vrain', 'SVVSD' or 'Longmont'.",
            },
        },
        "required": ["district"],
        "additionalProperties": False,
    },
)
def find_eap(district: str) -> dict:
    """Look up a Colorado school district's employee assistance program (EAP): provider,
    free counseling sessions, phone, how to log in, who is covered, when it was last
    verified and the official sources. Matches district name, short name or city, and
    tolerates typos. Entries the directory could not confirm say to ask HR.

    Returns the same fields as the district's page on memnon.app, plus its URL and 988.
    """
    return eap_directory.find_eap(district)


def _error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _call_tool(request_id: Any, params: dict, log: Callable[[str], None]) -> dict:
    name = params.get("name")
    entry = _TOOLS.get(name)
    if not entry:
        return _error(request_id, INVALID_PARAMS, f"Unknown tool: {name}")
    arguments = params.get("arguments") or {}
    schema = entry["definition"]["inputSchema"]
    missing = [k for k in schema.get("required", []) if not isinstance(arguments.get(k), str)]
    if missing or set(arguments) - set(schema["properties"]):
        return _error(request_id, INVALID_PARAMS, f"{name} takes: {', '.join(schema['properties'])}")
    log(name)
    try:
        output = entry["fn"](**arguments)
    except Exception as exc:  # a tool failure is a result, not a protocol error
        print(f"[mcp] tool={name} failed: {type(exc).__name__}")
        return _result(request_id, {"content": [{"type": "text", "text": "The lookup failed. Try again."}], "isError": True})
    return _result(request_id, {
        "content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False, indent=2)}],
        "structuredContent": output,
        "isError": False,
    })


def handle_message(message: Any, log: Callable[[str], None]) -> dict | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        if isinstance(message, dict) and "method" not in message and ("result" in message or "error" in message):
            return None  # a response to a request we never send
        return _error(message.get("id") if isinstance(message, dict) else None, INVALID_REQUEST, "Invalid request")
    if "id" not in message:
        return None  # notifications/initialized and friends need no reply
    request_id, method = message["id"], message["method"]
    params = message.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        return _result(request_id, {
            "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": [t["definition"] for t in _TOOLS.values()]})
    if method == "tools/call":
        return _call_tool(request_id, params, log)
    return _error(request_id, METHOD_NOT_FOUND, f"Method not found: {method}")


def handle_http(method: str, body: bytes, uid: str = "anonymous") -> tuple[int, Any]:
    """(status, JSON body or None) for one HTTP request to /api/mcp."""
    if method != "POST":
        # No server-initiated stream and no sessions to delete.
        return 405, None

    def log(tool_name: str) -> None:
        print(f"[mcp] tool={tool_name} uid={uid}")

    try:
        payload = json.loads(body or b"")
    except ValueError:
        return 400, _error(None, PARSE_ERROR, "Parse error")
    if isinstance(payload, list):
        if not payload:
            return 400, _error(None, INVALID_REQUEST, "Empty batch")
        responses = [r for r in (handle_message(m, log) for m in payload) if r is not None]
        return (200, responses) if responses else (202, None)
    response = handle_message(payload, log)
    return (200, response) if response is not None else (202, None)
