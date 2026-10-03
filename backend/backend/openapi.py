"""Print the OpenAPI schema, WebSocket events included, as JSON.

    python -m backend.openapi > schema.json

The client's `gen:types` script turns it into TypeScript types.
"""

import json

from backend.main import app

if __name__ == "__main__":
    print(json.dumps(app.openapi(), indent=2))
