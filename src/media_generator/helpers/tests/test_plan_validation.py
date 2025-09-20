"""
Unit tests for schema and invariants. No network.
Run: pytest -q
"""

import json
from pathlib import Path
from validators.json_validate import load_schema, parse_and_validate

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = load_schema(ROOT / "schemas" / "plan_schema.json")

def test_valid_minimal_plan():
    sample = {
        "title": "OK",
        "clips": [
            {"index":1,"start_sec":0,"end_sec":10,"dialogue":"a","verse_refs":["Psalm 1:1"],"image_prompt":"x","subtitle":"y"},
            {"index":2,"start_sec":10,"end_sec":20,"dialogue":"a","verse_refs":["Psalm 1:1"],"image_prompt":"x","subtitle":"y"},
            {"index":3,"start_sec":20,"end_sec":30,"dialogue":"a","verse_refs":["Psalm 1:1"],"image_prompt":"x","subtitle":"y"},
            {"index":4,"start_sec":30,"end_sec":40,"dialogue":"a","verse_refs":["Psalm 1:1"],"image_prompt":"x","subtitle":"y"},
            {"index":5,"start_sec":40,"end_sec":50,"dialogue":"a","verse_refs":["Psalm 1:1"],"image_prompt":"x","subtitle":"y"},
            {"index":6,"start_sec":50,"end_sec":60,"dialogue":"a","verse_refs":["Psalm 1:1"],"image_prompt":"x","subtitle":"y"}
        ]
    }
    s = json.dumps(sample)
    out = parse_and_validate(s, SCHEMA)
    assert out["title"] == "OK"

def test_wrong_count_rejected():
    bad = {"title":"bad","clips":[{"index":1,"start_sec":0,"end_sec":10,"dialogue":"a","verse_refs":["Ps 1:1"],"image_prompt":"x","subtitle":"y"}]}
    try:
        parse_and_validate(json.dumps(bad), SCHEMA)
        assert False, "expected failure"
    except ValueError as e:
        assert "exactly 6 clips" in str(e)

def test_wrong_duration_rejected():
    bad = {
        "title":"bad",
        "clips":[
            {"index":1,"start_sec":0,"end_sec":11,"dialogue":"a","verse_refs":["Ps 1:1"],"image_prompt":"x","subtitle":"y"},
            {"index":2,"start_sec":10,"end_sec":20,"dialogue":"a","verse_refs":["Ps 1:1"],"image_prompt":"x","subtitle":"y"},
            {"index":3,"start_sec":20,"end_sec":30,"dialogue":"a","verse_refs":["Ps 1:1"],"image_prompt":"x","subtitle":"y"},
            {"index":4,"start_sec":30,"end_sec":40,"dialogue":"a","verse_refs":["Ps 1:1"],"image_prompt":"x","subtitle":"y"},
            {"index":5,"start_sec":40,"end_sec":50,"dialogue":"a","verse_refs":["Ps 1:1"],"image_prompt":"x","subtitle":"y"},
            {"index":6,"start_sec":50,"end_sec":60,"dialogue":"a","verse_refs":["Ps 1:1"],"image_prompt":"x","subtitle":"y"}
        ]
    }
    try:
        parse_and_validate(json.dumps(bad), SCHEMA)
        assert False, "expected failure"
    except ValueError as e:
        assert "10 seconds" in str(e)
