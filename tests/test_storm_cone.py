"""Tests for core.storm_cone — StormCone geometry and serialization."""

from datetime import datetime, timedelta, timezone

from core.storm_cone import StormCone, _project, motion_from_fixes


class TestProject:
    """Test the spherical forward-projection helper."""

    def test_zero_distance_returns_origin(self):
        lat, lon = _project(35.0, -97.0, 180.0, 0.0)
        assert abs(lat - 35.0) < 1e-10
        assert abs(lon - (-97.0)) < 1e-10

    def test_due_north(self):
        lat, lon = _project(35.0, -97.0, 0.0, 60.0)
        # should move north → higher latitude
        assert lat > 35.0
        # longitude should stay roughly the same
        assert abs(lon - (-97.0)) < 0.1

    def test_due_east(self):
        lat, lon = _project(35.0, -97.0, 90.0, 60.0)
        # should move east → more positive longitude
        assert lon > -97.0
        # latitude should stay roughly the same
        assert abs(lat - 35.0) < 0.1

    def test_symmetry_north_south(self):
        lat_n, _ = _project(0.0, 0.0, 0.0, 100.0)
        lat_s, _ = _project(0.0, 0.0, 180.0, 100.0)
        assert abs(lat_n + lat_s) < 1e-8  # symmetric about equator


class TestStormCone:
    def _make_cone(self, heading=180, speed_kts=30):
        return StormCone(
            id="test1234",
            lat=35.0,
            lon=-97.0,
            heading=heading,
            speed_kts=speed_kts,
            creator="tester",
            created_at=datetime(2026, 4, 6, 20, 0, tzinfo=timezone.utc),
        )

    def test_dist(self):
        cone = self._make_cone(speed_kts=60)
        assert cone._dist(0.5) == 30.0
        assert cone._dist(1.0) == 60.0
        assert cone._dist(0.0) == 0.0

    def test_geojson_structure(self):
        cone = self._make_cone()
        gj = cone.build_geojson()
        assert gj["type"] == "FeatureCollection"
        features = gj["features"]
        # should have: 1 cone polygon, 3 ribs, 4 labels (15/30/45/60 min)
        types = [f["properties"]["ft"] for f in features]
        assert types.count("cone") == 1
        assert types.count("rib") == 3
        assert types.count("label") == 4

    def test_geojson_cone_is_closed_polygon(self):
        cone = self._make_cone()
        gj = cone.build_geojson()
        cone_feat = [f for f in gj["features"] if f["properties"]["ft"] == "cone"][0]
        ring = cone_feat["geometry"]["coordinates"][0]  # outer ring is nested
        # geoJSON polygon ring: first == last
        assert ring[0] == ring[-1]

    def test_zero_speed_cone(self):
        cone = self._make_cone(speed_kts=0)
        gj = cone.build_geojson()
        # with zero speed, no ribs and no labels
        types = [f["properties"]["ft"] for f in gj["features"]]
        assert types.count("rib") == 0
        assert types.count("label") == 0

    def test_label_times(self):
        cone = self._make_cone()
        gj = cone.build_geojson()
        labels = [f["properties"]["text"] for f in gj["features"]
                  if f["properties"]["ft"] == "label"]
        assert "2015Z" in labels  # 20:00 + 15 min
        assert "2030Z" in labels  # 20:00 + 30 min
        assert "2045Z" in labels  # 20:00 + 45 min
        assert "2100Z" in labels  # 20:00 + 60 min

    def test_label_times_use_valid_at(self):
        cone = self._make_cone()
        cone.valid_at = datetime(2026, 4, 6, 20, 10, tzinfo=timezone.utc)
        gj = cone.build_geojson()
        labels = [f["properties"]["text"] for f in gj["features"]
                  if f["properties"]["ft"] == "label"]
        assert "2025Z" in labels
        assert "2110Z" in labels

    def test_to_dict_roundtrip(self):
        cone = self._make_cone()
        d = cone.to_dict()
        restored = StormCone.from_dict(d)
        assert restored.id == cone.id
        assert restored.lat == cone.lat
        assert restored.lon == cone.lon
        assert restored.heading == cone.heading
        assert restored.speed_kts == cone.speed_kts
        assert restored.creator == cone.creator
        assert restored.valid_at == cone.valid_at

    def test_from_dict_missing_creator(self):
        d = {
            "id": "abc", "lat": 35.0, "lon": -97.0,
            "heading": 270, "speed_kts": 40,
        }
        cone = StormCone.from_dict(d)
        assert cone.creator == "unknown"

    def test_new_factory(self):
        cone = StormCone.new(35.0, -97.0, 180, 50, creator="bobby")
        assert len(cone.id) == 8
        assert cone.creator == "bobby"
        assert cone.speed_kts == 50


class TestStormConeMotion:
    def test_motion_from_fixes_due_east(self):
        t1 = datetime(2026, 4, 6, 20, 0, tzinfo=timezone.utc)
        t2 = t1 + timedelta(minutes=30)
        lat2, lon2 = _project(35.0, -97.0, 90.0, 20.0)
        motion = motion_from_fixes(35.0, -97.0, t1, lat2, lon2, t2)
        assert abs(motion["distance_nm"] - 20.0) < 0.1
        assert abs(motion["speed_kts"] - 40.0) < 0.2
        assert abs(motion["travel_bearing"] - 90.0) < 0.2
        assert abs(motion["heading"] - 270.0) < 0.2

    def test_motion_requires_later_second_fix(self):
        t = datetime(2026, 4, 6, 20, 0, tzinfo=timezone.utc)
        try:
            motion_from_fixes(35.0, -97.0, t, 35.1, -96.9, t)
        except ValueError:
            return
        assert False, "expected ValueError for same-time fixes"
