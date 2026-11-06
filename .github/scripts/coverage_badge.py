"""Turn coverage.xml into a shields.io endpoint JSON (lines + branches, same total as coverage)."""

import json
import math
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
covered = int(root.get("lines-covered")) + int(root.get("branches-covered"))
valid = int(root.get("lines-valid")) + int(root.get("branches-valid"))
pct = 100 * covered / valid if valid else 100
color = "brightgreen" if pct >= 95 else "green" if pct >= 85 else "yellow"
badge = {"schemaVersion": 1, "label": "coverage", "message": f"{math.floor(pct)}%", "color": color}
with open(sys.argv[2], "w") as f:
    json.dump(badge, f)
print(badge["message"])
