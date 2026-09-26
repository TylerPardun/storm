from datetime import datetime, timezone

import pandas as pd

import pytest

from core.storm_track import (
    TrackPoint, case_id_for, new_track_path, read_track_file, track_filename,
    write_track_csv, write_track_excel,
)


def test_track_filename_uses_the_first_points_date_and_time():
    t = datetime(2024, 4, 27, 20, 0, 12, tzinfo=timezone.utc)
    assert track_filename(t) == "storm_20240427_2000_track.csv"
    assert track_filename(t, ext="xlsx") == "storm_20240427_2000_track.xlsx"


def _points():
    return [
        TrackPoint(
            point_id=1,
            time=datetime(2024, 4, 27, 20, 0, 0, tzinfo=timezone.utc),
            lat=35.123456, lon=-97.654321,
            radar_site="KTLX", product="N0B", product_label="Reflectivity",
            tilt_deg=0.5,
        ),
        TrackPoint(
            point_id=2,
            time=datetime(2024, 4, 27, 20, 5, 0, tzinfo=timezone.utc),
            lat=35.2, lon=-97.7,
            radar_site="NOXP", product="VEL", product_label="Velocity",
            tilt_deg=None,
        ),
    ]


def test_write_track_csv_round_trips_every_field(tmp_path):
    path = tmp_path / "track.csv"
    write_track_csv(_points(), path)

    df = pd.read_csv(path)
    # MESO-VIEW's own columns first (its reader needs them), then STORM's
    assert list(df.columns) == [
        "point_id", "time", "lat", "lon", "source", "case_id", "track_file_kind", "edited_at",
        "radar_site", "product", "product_label", "tilt_deg",
    ]
    assert set(df["track_file_kind"]) == {"complete"}
    assert df["point_id"].tolist() == [1, 2]
    assert df["time"].tolist() == ["2024-04-27 20:00:00", "2024-04-27 20:05:00"]
    assert df["lat"].tolist() == [35.123456, 35.2]
    assert df["lon"].tolist() == [-97.654321, -97.7]
    assert df["radar_site"].tolist() == ["KTLX", "NOXP"]
    assert df["product"].tolist() == ["N0B", "VEL"]
    assert df["product_label"].tolist() == ["Reflectivity", "Velocity"]
    assert df["tilt_deg"].tolist()[0] == 0.5
    assert pd.isna(df["tilt_deg"].tolist()[1])


def test_write_track_excel_round_trips_every_field(tmp_path):
    path = tmp_path / "track.xlsx"
    write_track_excel(_points(), path)

    df = pd.read_excel(path, engine="openpyxl")
    assert df["point_id"].tolist() == [1, 2]
    assert df["radar_site"].tolist() == ["KTLX", "NOXP"]
    assert df["product_label"].tolist() == ["Reflectivity", "Velocity"]
    assert df["tilt_deg"].tolist()[0] == 0.5
    assert pd.isna(df["tilt_deg"].tolist()[1])


def test_write_track_csv_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "track.csv"
    write_track_csv(_points()[:1], path)
    assert path.exists()


def test_write_track_csv_rounds_coordinates_to_six_decimals(tmp_path):
    path = tmp_path / "track.csv"
    point = TrackPoint(
        point_id=1,
        time=datetime(2024, 4, 27, tzinfo=timezone.utc),
        lat=35.1234567891, lon=-97.1234567891,
        radar_site="KTLX", product="N0B", product_label="Reflectivity",
        tilt_deg=0.5,
    )
    write_track_csv([point], path)
    df = pd.read_csv(path)
    assert df["lat"].tolist() == [35.123457]
    assert df["lon"].tolist() == [-97.123457]


# A real MESO-VIEW track file, as written by its save_storm_track().
_MESO_VIEW_CSV = """point_id,time,lat,lon,source,case_id,track_file_kind,edited_at
36,2019-05-28 22:00:00,39.031152305381084,-99.06405791041178,manual,T10,complete,2026-05-31 22:29:23
35,2019-05-28 22:02:00,39.03413647079097,-99.04869120453317,manual,T10,complete,2026-05-31 22:29:23
34,2019-05-28 22:04:30,39.04905540764389,-99.02372030748072,moved_retimed,T10,complete,2026-05-31 22:29:23
"""


def test_meso_view_track_files_load(tmp_path):
    path = tmp_path / "T10_20190528_2200_autosave_edited.csv"
    path.write_text(_MESO_VIEW_CSV)

    points = read_track_file(path)

    assert [p.point_id for p in points] == [36, 35, 34]
    assert points[0].time == datetime(2019, 5, 28, 22, 0, tzinfo=timezone.utc)
    assert round(points[2].lat, 6) == 39.049055
    assert points[2].source == "moved_retimed"
    assert points[0].radar_site == "" and points[0].tilt_deg is None
    assert case_id_for(path) == "T10"


def test_hand_made_tables_with_other_column_names_load_in_time_order(tmp_path):
    path = tmp_path / "track.csv"
    path.write_text("Latitude,Longitude,DateTime\n35.2,-97.7,2024-04-27 20:05\n35.1,-97.6,2024-04-27 20:00\n,,bad\n")

    points = read_track_file(path)

    assert [(p.lat, p.time.minute) for p in points] == [(35.1, 0), (35.2, 5)]
    assert [p.point_id for p in points] == [1, 2]
    assert case_id_for(path) == ""


def test_saved_tracks_load_back_unchanged(tmp_path):
    for name, writer in (("t.csv", write_track_csv), ("t.xlsx", write_track_excel)):
        path = tmp_path / name
        writer(_points(), path, case_id="T37")
        assert read_track_file(path) == [
            TrackPoint(**{**p.__dict__, "source": "manual"}) for p in _points()
        ]
        assert set(pd.read_csv(path)["case_id"]) == {"T37"} if name.endswith(".csv") else True


def test_a_file_without_times_or_positions_is_refused(tmp_path):
    path = tmp_path / "notes.csv"
    path.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="time, lat, lon"):
        read_track_file(path)


def test_new_tracks_never_overwrite_an_existing_file(tmp_path):
    t = datetime(2024, 4, 27, 20, 0, tzinfo=timezone.utc)
    first = new_track_path(t, tmp_path)
    first.write_text("x")
    second = new_track_path(t, tmp_path)
    second.write_text("x")
    third = new_track_path(t, tmp_path)
    assert (first.name, second.name, third.name) == (
        "storm_20240427_2000_track.csv", "storm_20240427_2000_track_2.csv", "storm_20240427_2000_track_3.csv")
