"""Tests for core.annotation — Annotation model and type lookups."""


from core.annotation import (
    Annotation, ANNOTATION_TYPES, ANNOTATION_TYPE_MAP,
)


class TestAnnotationTypes:
    def test_all_keys_unique(self):
        keys = [t["key"] for t in ANNOTATION_TYPES]
        assert len(keys) == len(set(keys))

    def test_map_matches_list(self):
        for t in ANNOTATION_TYPES:
            assert ANNOTATION_TYPE_MAP[t["key"]] is t

    def test_required_fields(self):
        for t in ANNOTATION_TYPES:
            assert "key" in t
            assert "label" in t
            assert "symbol" in t
            assert "color" in t

    def test_pressure_types_present(self):
        assert ANNOTATION_TYPE_MAP["high_pressure"]["symbol"] == "H"
        assert ANNOTATION_TYPE_MAP["high_pressure"]["color"] == "#4A9EFF"
        assert ANNOTATION_TYPE_MAP["low_pressure"]["symbol"] == "L"
        assert ANNOTATION_TYPE_MAP["low_pressure"]["color"] == "#E53935"


class TestAnnotation:
    def test_new_factory(self):
        ann = Annotation.new("road_closure", lat=35.0, lon=-97.0)
        assert len(ann.id) == 8
        assert ann.type_key == "road_closure"
        assert ann.label == "Road Closure"
        assert ann.creator == "local"

    def test_new_custom_label(self):
        ann = Annotation.new("debris", lat=35.0, lon=-97.0, label="Tree down")
        assert ann.label == "Tree down"

    def test_to_dict_roundtrip(self):
        ann = Annotation.new("flooded", lat=35.5, lon=-97.5, ttl_hours=6)
        d = ann.to_dict()
        restored = Annotation.from_dict(d)
        assert restored.id == ann.id
        assert restored.type_key == "flooded"
        assert restored.lat == 35.5
        assert restored.lon == -97.5
        assert restored.ttl_hours == 6

    def test_from_dict_missing_creator(self):
        d = {
            "id": "abc12345",
            "type_key": "debris",
            "lat": 35.0,
            "lon": -97.0,
        }
        ann = Annotation.from_dict(d)
        assert ann.creator == "unknown"
        assert ann.label == ""

    def test_from_dict_iso_timestamp(self):
        d = {
            "id": "test1234",
            "type_key": "construction",
            "lat": 35.0,
            "lon": -97.0,
            "created_at": "2026-04-06T18:00:00+00:00",
        }
        ann = Annotation.from_dict(d)
        assert ann.created_at.year == 2026
        assert ann.created_at.month == 4

    def test_from_dict_none_timestamp_defaults_to_now(self):
        d = {
            "id": "test1234",
            "type_key": "construction",
            "lat": 35.0,
            "lon": -97.0,
        }
        ann = Annotation.from_dict(d)
        assert ann.created_at is not None
