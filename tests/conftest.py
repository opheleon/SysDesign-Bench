from pathlib import Path

import pytest
import yaml

from sdbench.schema import Catalogs, Scenario, load_catalogs

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = REPO_ROOT / "catalogs"
SCENARIO_DIR = REPO_ROOT / "scenarios" / "public"
PROOF_SCENARIO = SCENARIO_DIR / "retail-inventory-lookup-001.yaml"


@pytest.fixture(scope="session")
def catalogs() -> Catalogs:
    return load_catalogs(CATALOG_DIR)


@pytest.fixture()
def proof_scenario_dict() -> dict:
    with open(PROOF_SCENARIO, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture()
def proof_scenario(proof_scenario_dict: dict) -> Scenario:
    return Scenario.model_validate(proof_scenario_dict)
