"""Tests for capability registry validation and API."""
import json
import pytest
from pathlib import Path
import sys
import hashlib
import re

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

WORK_DIR = Path(__file__).parent.parent.parent
CARDS_DIR = WORK_DIR / "docs" / "capability_registry" / "cards"
SCHEMA_PATH = WORK_DIR / "docs" / "capability_card.schema.json"
RED_TEAM_PATH = WORK_DIR / "docs" / "capability_registry" / "red_team" / "red_team_demo.json"


def load_schema():
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def load_card(filename):
    with open(CARDS_DIR / filename) as f:
        return json.load(f)


class TestSchema:
    """Test the JSON schema is valid and complete."""
    
    def test_schema_exists(self):
        assert SCHEMA_PATH.exists(), "Schema file not found"
    
    def test_schema_valid_json(self):
        schema = load_schema()
        assert "required" in schema
        assert "properties" in schema
    
    def test_schema_has_required_fields(self):
        schema = load_schema()
        required = schema.get("required", [])
        assert "validation_evidence" in required
        assert "card_id" in required
        assert "interface" in required
        assert "applicability" in required
        assert "failure_modes" in required


class TestCapabilityCards:
    """Test all capability cards pass validation."""
    
    def test_all_cards_exist(self):
        cards = list(CARDS_DIR.glob("*.json"))
        assert len(cards) == 5, f"Expected 5 cards, found {len(cards)}"
    
    def test_all_cards_valid_json(self):
        for card_file in CARDS_DIR.glob("*.json"):
            with open(card_file) as f:
                card = json.load(f)
            assert "card_id" in card
    
    def test_card_001_cylinder(self):
        card = load_card("CAP-001-tensorlbm-cylinder-re100.json")
        assert card["card_id"] == "CAP-001"
        assert card["repo"] == "TensorLBM"
        assert card["discipline"] == "general_fluid"
        assert "C_D_mean" in card["interface"]["output"]
        assert "St" in card["interface"]["output"]
        assert card["validation_evidence"]["prereg_md5"] == "af20c79970712132604371186f10e93b"
    
    def test_card_002_suboff(self):
        card = load_card("CAP-002-tensorlbm-suboff-resistance.json")
        assert card["card_id"] == "CAP-002"
        assert card["repo"] == "TensorLBM"
        assert card["discipline"] == "resistance"
        assert "tau" in card["interface"]["input"]
        # mission 1819: the old PASS here was unsupported (placeholder md5 + non-existent
        # report_id).  The only real evidence, report 1375, answers "是否 <3%：否" and has one
        # case above the 15% acceptance line, so the card honestly records HONEST_FAIL.
        assert card["validation_evidence"]["verification_status"] == "HONEST_FAIL"
        # Check four-tree anchor value
        ref = card["validation_evidence"]["reference_values"]["C_T_ballast"]
        assert abs(ref["value"] - 1.1290741584063078) < 1e-10
    
    def test_card_003_multiphase(self):
        card = load_card("CAP-003-tensorlbm-multiphase-wave10.json")
        assert card["card_id"] == "CAP-003"
        assert card["repo"] == "TensorLBM"
        assert card["discipline"] == "seakeeping"
        # mission 1819: no reproducible platform evidence exists for wave-10, so the card is
        # NOT_VERIFIED (declared-empty anchor) rather than HONEST_FAIL (which needs evidence).
        assert card["validation_evidence"]["verification_status"] == "NOT_VERIFIED"
        # Check honest fail note about droplet water entry
        notes = card.get("notes", "")
        assert "HONEST FAIL" in notes or "droplet" in notes.lower()
    
    def test_card_004_dtmb5415(self):
        card = load_card("CAP-004-tensorcad-dtmb5415.json")
        assert card["card_id"] == "CAP-004"
        assert card["repo"] == "TensorCAD"
        assert card["discipline"] == "geometry"
        ref = card["validation_evidence"]["reference_values"]["deviation_mean"]
        assert abs(ref["value"] - 0.236) < 0.001
        ref_pct = card["validation_evidence"]["reference_values"]["deviation_pct_L"]
        assert abs(ref_pct["value"] - 0.17) < 0.01
    
    def test_card_005_reporting(self):
        card = load_card("CAP-005-tensorplatform-report-generation.json")
        assert card["card_id"] == "CAP-005"
        assert card["repo"] == "TensorPlatform"
        assert card["discipline"] == "reporting"
        assert card["validation_evidence"]["verification_status"] == "PASS"


class TestValidationEvidence:
    """Test validation_evidence field requirements."""
    
    def test_all_cards_have_validation_evidence(self):
        for card_file in CARDS_DIR.glob("*.json"):
            with open(card_file) as f:
                card = json.load(f)
            assert "validation_evidence" in card, f"{card_file.name} missing validation_evidence"
            ve = card["validation_evidence"]
            assert "report_id" in ve, f"{card_file.name} missing report_id"
            assert "report_md5" in ve, f"{card_file.name} missing report_md5"
            assert "verification_status" in ve, f"{card_file.name} missing verification_status"
    
    def test_all_report_md5_valid_format(self):
        for card_file in CARDS_DIR.glob("*.json"):
            with open(card_file) as f:
                card = json.load(f)
            ve = card["validation_evidence"]
            if ve.get("anchor_type") == "none":
                # declared-empty anchor: there are no anchor bytes, so no digest at all
                assert ve.get("report_md5") is None, f"{card_file.name} invents a digest for an empty anchor"
                assert ve.get("gap_reason"), f"{card_file.name} empty anchor without gap_reason"
                continue
            md5 = ve["report_md5"]
            assert re.match(r"^[a-fA-F0-9]{32}$", md5), f"{card_file.name} has invalid md5: {md5}"
    
    def test_verification_status_valid(self):
        valid_statuses = {"PASS", "HONEST_FAIL", "FAIL", "IN_PROGRESS", "NOT_VERIFIED"}
        for card_file in CARDS_DIR.glob("*.json"):
            with open(card_file) as f:
                card = json.load(f)
            status = card["validation_evidence"]["verification_status"]
            assert status in valid_statuses, f"{card_file.name} has invalid status: {status}"


class TestRedTeam:
    """Test red team validation gate."""
    
    def test_red_team_demo_exists(self):
        assert RED_TEAM_PATH.exists(), "Red team demo file not found"
    
    def test_red_team_all_pass(self):
        with open(RED_TEAM_PATH) as f:
            demo = json.load(f)
        for test in demo["tests"]:
            assert test["verdict"] == "PASS", f"Red team test {test['test_id']} failed: {test['name']}"
    
    def test_missing_evidence_rejected(self):
        """RT-001: Card without validation_evidence must be rejected."""
        with open(RED_TEAM_PATH) as f:
            demo = json.load(f)
        rt001 = next(t for t in demo["tests"] if t["test_id"] == "RT-001")
        assert rt001["expected_result"] == "REJECTED"
        assert rt001["actual_result"] == "REJECTED"
    
    def test_fake_report_id_rejected(self):
        """RT-002: Card with non-existent report_id must be rejected."""
        with open(RED_TEAM_PATH) as f:
            demo = json.load(f)
        rt002 = next(t for t in demo["tests"] if t["test_id"] == "RT-002")
        assert rt002["expected_result"] == "REJECTED"
        assert rt002["actual_result"] == "REJECTED"


class TestCardFields:
    """Test specific field requirements."""
    
    def test_card_id_format(self):
        for card_file in CARDS_DIR.glob("*.json"):
            with open(card_file) as f:
                card = json.load(f)
            assert re.match(r"^CAP-[A-Za-z0-9_-]+$", card["card_id"]), \
                f"{card_file.name} has invalid card_id: {card['card_id']}"
    
    def test_version_semver(self):
        for card_file in CARDS_DIR.glob("*.json"):
            with open(card_file) as f:
                card = json.load(f)
            assert re.match(r"^[0-9]+\.[0-9]+\.[0-9]+$", card["version"]), \
                f"{card_file.name} has invalid version: {card['version']}"
    
    def test_interface_complete(self):
        for card_file in CARDS_DIR.glob("*.json"):
            with open(card_file) as f:
                card = json.load(f)
            iface = card["interface"]
            assert "input" in iface
            assert "output" in iface
            assert "format" in iface
    
    def test_failure_modes_present(self):
        for card_file in CARDS_DIR.glob("*.json"):
            with open(card_file) as f:
                card = json.load(f)
            modes = card["failure_modes"]
            assert len(modes) > 0, f"{card_file.name} has no failure modes"
            for mode in modes:
                assert "mode" in mode
                assert "symptom" in mode
                assert "mitigation" in mode


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
