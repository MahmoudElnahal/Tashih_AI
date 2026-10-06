"""Write the OpenAPI schema to docs/openapi.json (``make openapi``)."""

from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.main import create_app

OUTPUT = Path("docs/openapi.json")


def main() -> None:
    app = create_app(Settings(_env_file=None, openai_api_key=""), sources=[])
    schema = app.openapi()
    OUTPUT.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {OUTPUT} ({len(schema['paths'])} paths)")


if __name__ == "__main__":
    main()
