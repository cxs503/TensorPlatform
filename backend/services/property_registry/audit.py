"""Coverage audit: scan modules for hardcoded physical constants.

Identifies numeric constants that look like physical properties but are not
registered in the property registry. The debt ledger only shrinks (monotone
decreasing gate).
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# Patterns that suggest a numeric constant is a physical property
_PHYSICAL_PATTERNS = [
    # Density
    (r"(?i)(rho|density|ρ).*\d+\.?\d*[eE]?[+-]?\d*", "density"),
    # Viscosity
    (r"(?i)(nu|mu|viscosity|ν|μ).*\d+\.?\d*[eE]?[+-]?\d*", "viscosity"),
    # Gravity
    (r"(?i)(gravity|g_m_s2|g_ms2|grav).*9\.8", "gravity"),
    # Known physical values
    (r"=\s*1025\.0", "sea_water_density"),
    (r"=\s*998\.0", "fresh_water_density"),
    (r"=\s*1\.004e-6", "fresh_water_viscosity"),
    (r"=\s*1\.1892e-6", "sea_water_viscosity"),
    (r"=\s*9\.81\b", "gravity"),
    (r"=\s*9\.80665\b", "gravity_standard"),
]

# Known physical constant ranges (to filter out non-physical numbers)
_PHYSICAL_RANGES = {
    "density": (500.0, 2500.0),  # kg/m³ for common materials
    "viscosity": (1e-8, 1e-1),  # m²/s for kinematic viscosity
    "gravity": (9.0, 10.5),  # m/s²
}


class DebtLedger:
    """Tracks unregistered physical constants (debt).
    
    The ledger only shrinks: once a constant is identified, it stays in the
    ledger until it's registered. This enforces the monotone decreasing gate.
    """
    
    def __init__(self, ledger_path: Optional[Path] = None):
        self.ledger_path = ledger_path or Path("property_debt_ledger.json")
        self._debt: Dict[str, List[Dict[str, Any]]] = {}
        self._load()
    
    def _load(self) -> None:
        """Load existing ledger from disk."""
        if self.ledger_path.exists():
            try:
                with open(self.ledger_path, "r") as f:
                    content = f.read().strip()
                    if content:
                        self._debt = json.loads(content)
                    else:
                        self._debt = {}
            except (json.JSONDecodeError, IOError):
                self._debt = {}
    
    def _save(self) -> None:
        """Save ledger to disk."""
        with open(self.ledger_path, "w") as f:
            json.dump(self._debt, f, indent=2)
    
    def add_debt(self, module_path: str, constant_name: str, value: float, line_no: int) -> None:
        """Add an unregistered constant to the debt ledger."""
        if module_path not in self._debt:
            self._debt[module_path] = []
        
        # Check if already in ledger
        for entry in self._debt[module_path]:
            if entry["name"] == constant_name and entry["value"] == value:
                return  # Already there
        
        self._debt[module_path].append({
            "name": constant_name,
            "value": value,
            "line": line_no,
            "status": "unregistered",
        })
        self._save()
    
    def mark_registered(self, module_path: str, constant_name: str, attr_id: str) -> None:
        """Mark a constant as registered (reduces debt)."""
        if module_path not in self._debt:
            return
        
        for entry in self._debt[module_path]:
            if entry["name"] == constant_name:
                entry["status"] = "registered"
                entry["attr_id"] = attr_id
                break
        
        self._save()
    
    def get_debt(self, module_path: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
        """Get current debt ledger (optionally filtered by module)."""
        if module_path:
            return {module_path: self._debt.get(module_path, [])}
        return self._debt
    
    def get_unregistered_count(self, module_path: Optional[str] = None) -> int:
        """Count unregistered constants."""
        debt = self.get_debt(module_path)
        count = 0
        for entries in debt.values():
            for entry in entries:
                if entry.get("status") == "unregistered":
                    count += 1
        return count
    
    def total_debt(self) -> int:
        """Total number of unregistered constants across all modules."""
        return self.get_unregistered_count()


def _is_physical_constant(name: str, value: float) -> Optional[str]:
    """Check if a constant name/value looks like a physical property.
    
    Returns the category if it looks physical, None otherwise.
    """
    name_lower = name.lower()
    
    # Check for sea water density
    if ("sea" in name_lower or "salt" in name_lower) and ("rho" in name_lower or "density" in name_lower):
        if _PHYSICAL_RANGES["density"][0] <= value <= _PHYSICAL_RANGES["density"][1]:
            return "sea_water_density"
    
    # Check for fresh water density
    if ("fresh" in name_lower) and ("rho" in name_lower or "density" in name_lower):
        if _PHYSICAL_RANGES["density"][0] <= value <= _PHYSICAL_RANGES["density"][1]:
            return "fresh_water_density"
    
    # Check for sea water viscosity
    if ("sea" in name_lower or "salt" in name_lower) and ("nu" in name_lower or "viscosity" in name_lower):
        if _PHYSICAL_RANGES["viscosity"][0] <= value <= _PHYSICAL_RANGES["viscosity"][1]:
            return "sea_water_viscosity"
    
    # Check for fresh water viscosity
    if ("fresh" in name_lower) and ("nu" in name_lower or "viscosity" in name_lower):
        if _PHYSICAL_RANGES["viscosity"][0] <= value <= _PHYSICAL_RANGES["viscosity"][1]:
            return "fresh_water_viscosity"
    
    # Check for gravity
    if "g_" in name_lower or "gravity" in name_lower or "grav" in name_lower:
        if _PHYSICAL_RANGES["gravity"][0] <= value <= _PHYSICAL_RANGES["gravity"][1]:
            return "gravity"
    
    # Check known values
    if abs(value - 1025.0) < 0.1:
        return "sea_water_density"
    if abs(value - 998.0) < 0.1:
        return "fresh_water_density"
    if abs(value - 1.004e-6) < 1e-9:
        return "fresh_water_viscosity"
    if abs(value - 1.1892e-6) < 1e-9:
        return "sea_water_viscosity"
    if abs(value - 9.81) < 0.01:
        return "gravity"
    if abs(value - 9.80665) < 0.001:
        return "gravity_standard"
    
    # Generic density check
    if "rho" in name_lower or "density" in name_lower:
        if _PHYSICAL_RANGES["density"][0] <= value <= _PHYSICAL_RANGES["density"][1]:
            return "density"
    
    # Generic viscosity check
    if "nu" in name_lower or "viscosity" in name_lower:
        if _PHYSICAL_RANGES["viscosity"][0] <= value <= _PHYSICAL_RANGES["viscosity"][1]:
            return "viscosity"
    
    return None


def _extract_constants_from_ast(tree: ast.AST) -> List[Tuple[str, float, int]]:
    """Extract numeric constants from AST.
    
    Returns list of (name, value, line_number).
    """
    constants = []
    
    for node in ast.walk(tree):
        # Module-level assignments: NAME = value
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, (ast.Constant, ast.Num)):
                    name = target.id
                    # Get value
                    if isinstance(node.value, ast.Constant):
                        value = node.value.value
                    else:  # ast.Num (Python < 3.8)
                        value = node.value.n
                    
                    if isinstance(value, (int, float)):
                        constants.append((name, float(value), node.lineno))
        
        # Annotated assignments: NAME: type = value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name) and isinstance(node.value, (ast.Constant, ast.Num)):
                name = node.target.id
                if isinstance(node.value, ast.Constant):
                    value = node.value.value
                else:
                    value = node.value.n
                
                if isinstance(value, (int, float)):
                    constants.append((name, float(value), node.lineno))
    
    return constants


def audit_module_constants(
    module_path: str,
    registry_attr_ids: Optional[Set[str]] = None,
    ledger: Optional[DebtLedger] = None
) -> Dict[str, Any]:
    """Audit a Python module for hardcoded physical constants.
    
    Scans the module for numeric constants that look like physical properties.
    Checks if they're registered in the property registry.
    Unregistered constants are added to the debt ledger.
    
    Args:
        module_path: Path to the Python module to audit
        registry_attr_ids: Set of registered attr_ids (if None, all are unregistered)
        ledger: Debt ledger to update (if None, creates a new one)
    
    Returns:
        Dict with audit results:
        - total_constants: total numeric constants found
        - physical_constants: constants that look physical
        - registered: constants that are registered
        - unregistered: constants that are not registered (debt)
        - coverage_pct: percentage of physical constants that are registered
    """
    path = Path(module_path)
    if not path.exists():
        raise FileNotFoundError(f"Module not found: {module_path}")
    
    with open(path, "r") as f:
        source = f.read()
    
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {
            "error": f"Syntax error in {module_path}: {e}",
            "total_constants": 0,
            "physical_constants": 0,
            "registered": 0,
            "unregistered": 0,
            "coverage_pct": 0.0,
        }
    
    constants = _extract_constants_from_ast(tree)
    
    registry_attr_ids = registry_attr_ids or set()
    ledger = ledger or DebtLedger()
    
    physical = []
    registered = []
    unregistered = []
    
    for name, value, line_no in constants:
        category = _is_physical_constant(name, value)
        if category is None:
            continue
        
        physical.append({
            "name": name,
            "value": value,
            "line": line_no,
            "category": category,
        })
        
        # Check if registered (by name heuristic)
        # In practice, you'd map constant names to attr_ids
        attr_id = _guess_attr_id(name, category)
        if attr_id in registry_attr_ids:
            registered.append({
                "name": name,
                "value": value,
                "attr_id": attr_id,
            })
            ledger.mark_registered(module_path, name, attr_id)
        else:
            unregistered.append({
                "name": name,
                "value": value,
                "line": line_no,
                "category": category,
            })
            ledger.add_debt(module_path, name, value, line_no)
    
    total_physical = len(physical)
    coverage_pct = (len(registered) / total_physical * 100.0) if total_physical > 0 else 100.0
    
    return {
        "module_path": module_path,
        "total_constants": len(constants),
        "physical_constants": total_physical,
        "registered": len(registered),
        "unregistered": len(unregistered),
        "coverage_pct": coverage_pct,
        "registered_details": registered,
        "unregistered_details": unregistered,
    }


def _guess_attr_id(constant_name: str, category: str) -> str:
    """Guess the attr_id for a constant based on its name and category.
    
    This is a heuristic - in practice, you'd have an explicit mapping.
    """
    name_lower = constant_name.lower()
    
    if category == "sea_water_density" or ("sea" in name_lower and "rho" in name_lower):
        return "rho_sea_water_kg_m3"
    if category == "fresh_water_density" or ("fresh" in name_lower and "rho" in name_lower):
        return "rho_fresh_water_kg_m3"
    if category == "sea_water_viscosity" or ("sea" in name_lower and "nu" in name_lower):
        return "nu_sea_water_m2_s"
    if category == "fresh_water_viscosity" or ("fresh" in name_lower and "nu" in name_lower):
        return "nu_fresh_water_m2_s"
    if category in ("gravity", "gravity_standard"):
        return "g_m_s2"
    
    # Generic fallback
    return f"{category}_{constant_name}"
