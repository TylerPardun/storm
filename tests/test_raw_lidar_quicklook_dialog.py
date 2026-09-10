import numpy as np

from archive.fetchers.raw_lidar_archive_fetcher import KNOWN_RAW_LIDAR_SOURCES, RawLidarRays
from ui.dialogs.raw_lidar_quicklook_dialog import RawLidarQuicklookDialog


def _rays(mobile=False):
    source = next(s for s in KNOWN_RAW_LIDAR_SOURCES if s.mobile == mobile and s.product == 'ppi')
    n = 5
    return RawLidarRays(
        source=source,
        time_epoch=np.arange(n, dtype=float) + 1_700_000_000.0,
        distance_m=np.array([100.0, 200.0, 300.0]),
        distance_kind='range',
        azimuth_deg=np.zeros(n), elevation_deg=np.full(n, 0.5),
        latitude=np.full(n, np.nan), longitude=np.full(n, np.nan),
        altitude_m=np.full(n, np.nan), heading_deg=np.full(n, np.nan),
        scan_number=np.arange(n),
        fields={'velocity': {'data': np.ma.masked_invalid(np.arange(n * 3, dtype=float).reshape(n, 3)), 'units': 'm/s'},
                'intensity': {'data': np.ma.masked_invalid(np.ones((n, 3))), 'units': ''}},
        housekeeping={}, coordinate_source=np.full(n, 'file rays'),
        position_time_epoch=np.full(n, np.nan), provenance={}, warnings=[],
    )


def test_field_selector_populates_from_preloaded_rays():
    dlg = RawLidarQuicklookDialog('CLAMPS1-PPI', preloaded_rays=_rays())
    fields = [dlg._field_combo.itemText(i) for i in range(dlg._field_combo.count())]
    assert fields == ['intensity', 'velocity']


def test_switching_field_updates_the_plotted_mesh():
    dlg = RawLidarQuicklookDialog('CLAMPS1-PPI', preloaded_rays=_rays())
    dlg._field_combo.setCurrentText('intensity')
    mesh_intensity = dlg._fig.axes[0].collections[0].get_array()
    dlg._field_combo.setCurrentText('velocity')
    mesh_velocity = dlg._fig.axes[0].collections[0].get_array()
    assert not np.array_equal(np.asarray(mesh_intensity), np.asarray(mesh_velocity))


def test_mobile_platform_status_shows_acquisition_without_geometry_claim():
    dlg = RawLidarQuicklookDialog('DLTRUCK1-DL1-PPI', preloaded_rays=_rays(mobile=True))
    assert '5 rays' in dlg._status_label.text()
    assert 'ground geometry' not in dlg._status_label.text()


def test_stationary_platform_status_omits_the_mobile_caveat():
    dlg = RawLidarQuicklookDialog('CLAMPS1-PPI', preloaded_rays=_rays(mobile=False))
    assert 'ground geometry' not in dlg._status_label.text()


def test_empty_dialog_with_no_preloaded_rays_does_not_crash():
    dlg = RawLidarQuicklookDialog('CLAMPS1-PPI')
    assert dlg._field_combo.count() == 0
    dlg.set_rays(_rays())
    assert dlg._field_combo.count() == 2
