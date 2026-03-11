"""Scenario manager — loads interview/call scenarios from JSON files.

Scenarios are stored in the `scenarios/` directory as JSON files.
Each scenario defines a system prompt, voice config, tools, and metadata.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Scenarios directory (sibling to this module)
SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def list_scenarios() -> list[dict]:
    """Return summary info for all available scenarios."""
    scenarios = []
    if not SCENARIOS_DIR.exists():
        return scenarios

    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            scenarios.append({
                "id": data["id"],
                "name": data["name"],
                "description": data.get("description", ""),
                "version": data.get("version", "1.0"),
            })
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Skipping invalid scenario file {path.name}: {e}")

    return scenarios


def load_scenario(scenario_id: str) -> Optional[dict]:
    """Load a full scenario by its ID."""
    if not SCENARIOS_DIR.exists():
        return None

    for path in SCENARIOS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("id") == scenario_id:
                return data
        except (json.JSONDecodeError, KeyError):
            continue

    return None
