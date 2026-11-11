"""
Load aircraft model rules from modelmap.txt (FSLTL ModelMatchRuleSet XML).

Each FSLTL variant has its own legacy aircraft.cfg title= entry (for example,
"FSLTL A20N BAW British Airways"). SimConnect EX1 receives that full title and an
empty livery string. Modular SimObjects do not have a unique title per livery;
selecting one requires livery enumeration and is not implemented by this title-based
model map yet.
"""

import os
import xml.etree.ElementTree as ET

DEFAULT_MODEL = "FSLTL_A20N_ZZZZ"
MODELMAP_FILENAME = "modelmap.txt"

_resolver = None


def pick_best_model(model_name, airline_prefix=None):
    """
    Choose one title= string from a // separated VMR model list.

    Airline-specific rules use space-separated titles (FSLTL A20N BAW British Airways).
    Type-only fallbacks use entries like FSLTL_A20N_ZZZZ as a single title.
    """
    if not model_name:
        return DEFAULT_MODEL

    options = [o.strip() for o in model_name.split("//") if o.strip()]
    options = [o for o in options if "STUB" not in o.upper()]
    if not options:
        return DEFAULT_MODEL

    prefix_u = (airline_prefix or "").upper()

    if prefix_u:
        for opt in options:
            if opt.startswith("FSLTL ") and prefix_u in opt.upper():
                return opt

    for opt in options:
        if opt.startswith("FSLTL "):
            return opt

    return options[0]


class ModelMapResolver:
    def __init__(self, path=None):
        base = os.path.dirname(os.path.abspath(__file__))
        self.path = path or os.path.join(base, MODELMAP_FILENAME)
        self.by_type = {}
        self.by_prefix_type = {}
        self.rule_count = 0
        self._load()

    @staticmethod
    def _norm_attrib(elem):
        return {k.strip(): (v.strip() if v else "") for k, v in elem.attrib.items()}

    def _load(self):
        if not os.path.isfile(self.path):
            raise FileNotFoundError(f"Model map not found: {self.path}")

        for _event, elem in ET.iterparse(self.path, events=("end",)):
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag != "ModelMatchRule":
                elem.clear()
                continue

            attrs = self._norm_attrib(elem)
            type_code = attrs.get("TypeCode", "").upper()
            model_raw = attrs.get("ModelName", "")
            prefix = attrs.get("CallsignPrefix", "").upper()

            if not type_code or not model_raw:
                elem.clear()
                continue

            if prefix:
                self.by_prefix_type[(prefix, type_code)] = model_raw
            else:
                self.by_type[type_code] = model_raw

            self.rule_count += 1
            elem.clear()

        print(
            f"[ModelMap] Loaded {self.rule_count:,} rules "
            f"({len(self.by_type):,} types, {len(self.by_prefix_type):,} airline+type) "
            f"from {os.path.basename(self.path)}"
        )

    def resolve_explicit(self, type_code, callsign=None):
        """Return a title only when a usable exact type rule exists."""
        t = (type_code or "").upper().strip()
        call = (callsign or "").strip().upper()
        prefix = call[:3] if len(call) >= 3 else ""

        if prefix:
            raw = self.by_prefix_type.get((prefix, t))
            if raw and any("STUB" not in option.upper()
                            for option in raw.split("//") if option.strip()):
                return pick_best_model(raw, airline_prefix=prefix)

        raw = self.by_type.get(t)
        if raw and any("STUB" not in option.upper()
                       for option in raw.split("//") if option.strip()):
            return pick_best_model(raw)
        return ""

    def resolve(self, type_code, callsign=None):
        """Return aircraft.cfg title= for ICAO type and flight number/callsign."""
        return self.resolve_explicit(type_code, callsign) or pick_best_model(
            self.by_type.get("A20N", DEFAULT_MODEL)
        )

    def has_explicit_rule(self, type_code, callsign=None):
        """Whether the map contains a usable exact type or airline+type rule."""
        return bool(self.resolve_explicit(type_code, callsign))

    def is_spawnable(self, type_code, callsign=None):
        t = (type_code or "").upper().strip()
        call = (callsign or "").strip().upper()
        prefix = call[:3] if len(call) >= 3 else ""
        if self.has_explicit_rule(t, call):
            return True
        if t in self.by_type or (prefix and (prefix, t) in self.by_prefix_type):
            return False
        return "STUB" not in self.resolve(t, call).upper()


def get_model_resolver(path=None):
    global _resolver
    if _resolver is None:
        _resolver = ModelMapResolver(path)
    return _resolver
