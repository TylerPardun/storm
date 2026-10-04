"""NOXP PPI gate rendering, sharing the lidar sector-safe rasterizer."""
import numpy as np
from archive.fetchers.raw_lidar_archive_fetcher import RawLidarRays, RawLidarSource
from archive.fetchers.noxp_radar_archive_fetcher import noxp_volume_to_scan
from ui.map.lidar_overlay import render_lidar_to_png
from core.noxp_radar_scan import field_meta


def render_noxp_to_png(volume, sweep, field, grid_size=768):
    scan = noxp_volume_to_scan(volume, sweep, field)
    idx = np.arange(int(volume.sweep_start[sweep]), int(volume.sweep_end[sweep]) + 1)
    n = len(idx)
    def values(array):
        array = np.asarray(array)
        return np.full(n, float(array.flat[0])) if array.size == 1 else array[idx]
    source = RawLidarSource('NOXP', 'NOXP', '', '', 'ppi', False)
    rays = RawLidarRays(source, volume.time_epoch[idx],
        volume.range_m[idx] if volume.range_m.ndim == 2 else volume.range_m,
        volume.azimuth_deg[idx], volume.elevation_deg[idx],
        values(volume.latitude), values(volume.longitude), values(volume.altitude_m),
        np.full(n, np.nan), np.zeros(n),
        {field: {**volume.fields[field], 'data': volume.fields[field]['data'][idx]}},
        {}, np.full(n, 'file'), volume.time_epoch[idx], volume.provenance, [])
    png, bounds, metadata = render_lidar_to_png(rays, scan.scan_time, field, grid_size,
                                              ray_indices=np.arange(n), style=field_meta(field))
    return png, bounds, scan, metadata
