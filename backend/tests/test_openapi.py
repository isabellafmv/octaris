"""The client's generated types must match the backend's models."""
import json
from pathlib import Path

from pydantic import TypeAdapter

from backend.main import app
from backend.schemas import WsEvent

GENERATED = Path(__file__).parents[2] / "client/src/renderer/src/generated/openapi.json"


def test_client_schema_is_up_to_date():
    assert json.loads(GENERATED.read_text()) == app.openapi(), (
        "The API changed: run `npm run gen:types` in client/ and commit the result"
    )


def test_event_types_are_in_the_schema():
    schemas = app.openapi()["components"]["schemas"]
    mapping = schemas["WsEvent"]["discriminator"]["mapping"]
    assert {"snapshot", "status", "progress", "stop", "serial_log", "printer", "print_end"} <= set(mapping)
    for ref in mapping.values():
        name = ref.rsplit("/", 1)[1]
        assert schemas[name]["required"][0] == "type"


def test_optional_fields_are_left_out_of_the_message():
    from backend.schemas import ProgressEvent, StopEvent

    assert StopEvent(resumable=False, reason="x").dump() == {
        "type": "stop", "resumable": False, "reason": "x",
    }
    assert ProgressEvent(lines_sent=1, lines_total=2).dump() == {
        "type": "progress", "lines_sent": 1, "lines_total": 2,
    }
    adapter = TypeAdapter(WsEvent)
    assert isinstance(adapter.validate_python({"type": "stop", "resumable": True, "reason": None, "line": 3}), StopEvent)
