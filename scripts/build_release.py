"""Build the flat integration archive expected by HACS zip_release."""

import json
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
integration = root / "custom_components/fems_sunspec_bridge"
version = json.loads((integration / "manifest.json").read_text())["version"]
if len(sys.argv) > 1 and sys.argv[1] != f"v{version}":
    raise SystemExit(f"Tag must match manifest version v{version}")
(root / "dist").mkdir(exist_ok=True)
with ZipFile(root / "dist/fems_sunspec_bridge.zip", "w", ZIP_DEFLATED) as archive:
    for path in sorted(integration.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            archive.write(path, path.relative_to(integration))
print(f"Built dist/fems_sunspec_bridge.zip for {version}")
