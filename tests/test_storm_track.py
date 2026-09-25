from datetime import datetime, timezone

import pandas as pd

from core.storm_track import (
    TrackPoint, track_filename, write_track_csv, write_track_excel,
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
    assert list(df.columns) == [
        "point_id", "time", "lat", "lon",
        "radar_site", "product", "product_label", "tilt_deg",
    ]
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
