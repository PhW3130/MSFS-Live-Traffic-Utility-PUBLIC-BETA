import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from model_map import ModelMapResolver


def _write_map(path):
    path.write_text(
        "<ModelMatchRuleSet>"
        '<ModelMatchRule TypeCode="A20N" ModelName="FSLTL A20N" />'
        '<ModelMatchRule TypeCode="A20N" CallsignPrefix="BAW" '
        'ModelName="FSLTL A20N BAW British Airways" />'
        '<ModelMatchRule TypeCode="B738" ModelName="STUB_B738" />'
        "</ModelMatchRuleSet>",
        encoding="utf-8",
    )


def test_explicit_model_map_rules_are_distinguished_from_fallback(tmp_path):
    model_map_path = tmp_path / "modelmap.txt"
    _write_map(model_map_path)
    resolver = ModelMapResolver(model_map_path)

    assert resolver.resolve_explicit("A20N", "BAW123") == "FSLTL A20N BAW British Airways"
    assert resolver.resolve_explicit("A20N", "DLH123") == "FSLTL A20N"
    assert resolver.resolve_explicit("B738", "RYR123") == ""
    assert not resolver.is_spawnable("B738", "RYR123")
    assert resolver.resolve("B738", "RYR123") == "FSLTL A20N"
