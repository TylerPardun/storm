"""Rasterize measured lidar cells, preserving partial sweeps and per-ray origins."""
from datetime import datetime, timezone
import io

import numpy as np
from PIL import Image, ImageDraw
from pyproj import Geod, Transformer


def render_lidar_to_png(rays, when, field='velocity', grid_size=768, *, ray_indices=None, style=None):
    """Return PNG, geographic bounds and acquisition metadata.

    Azimuth is used as declared in the file, without an additional heading
    rotation. Angular cell widths describe sampling support, not beam width.
    Co-pointing measurements are drawn as one-pixel beam strokes.
    """
    if field not in rays.fields:
        raise ValueError(f'No {field} field')
    if ray_indices is None:
        candidates = rays.rays_at(when, 90)
        if not len(candidates):
            raise ValueError('No recent lidar rays at this time')
        # Use only the latest contiguous scan, not every scan in a time window.
        end = candidates[-1]
        start = end
        while start > 0:
            previous = start - 1
            if (rays.time_epoch[start] - rays.time_epoch[previous] > 10
                    or rays.time_epoch[end] - rays.time_epoch[previous] > 90):
                break
            if not np.isfinite(rays.scan_number[end]) or rays.scan_number[previous] != rays.scan_number[end]:
                break
            start = previous
        idx = np.arange(start, end + 1)
    else:
        idx = np.asarray(ray_indices, dtype=int)
    idx = idx[rays.ground_geometry_valid[idx]]
    if not len(idx):
        if rays.provenance.get("north_referenced") is False:
            raise ValueError(rays.provenance.get("azimuth_reference", "Scan orientation unknown"))
        raise ValueError('No positioned rays with north-referenced azimuth')
    ranges = np.asarray(rays.distance_m)
    if ranges.ndim == 1:
        ranges = np.broadcast_to(ranges, (len(idx), len(ranges)))
    else:
        ranges = ranges[idx]
    if ranges.shape[-1] < 2 or not np.isfinite(ranges).all() or np.any(np.diff(ranges, axis=1) <= 0):
        raise ValueError('Map rendering requires increasing range gates')
    edges = np.concatenate((np.maximum(0, ranges[:, :1] - np.diff(ranges[:, :2], axis=1)/2),
                            (ranges[:, 1:]+ranges[:, :-1])/2,
                            ranges[:, -1:] + np.diff(ranges[:, -2:], axis=1)/2), axis=1)
    azimuth = rays.azimuth_deg[idx]
    # Estimate within-sweep spacing. Large gaps never become large filled cells.
    steps = np.abs(np.diff(np.rad2deg(np.unwrap(np.deg2rad(azimuth)))))
    steps = steps[(steps > .05) & (steps < 5)]
    half = min(float(np.median(steps))/2, 1.0) if len(steps) else 0.0
    elevation = rays.elevation_deg[idx]
    valid_elevation = (elevation >= 0) & (elevation < 89)
    idx, azimuth, elevation = idx[valid_elevation], azimuth[valid_elevation], elevation[valid_elevation]
    edges = edges[valid_elevation]
    if not len(idx):
        raise ValueError('No plan-view rays in this scan')
    geod = Geod(ellps='WGS84')
    merc = Transformer.from_crs(4326, 3857, always_xy=True)
    inv = Transformer.from_crs(3857, 4326, always_xy=True)
    distance = np.cos(np.deg2rad(elevation))[:, None] * edges
    shape = distance.shape
    lat = np.broadcast_to(rays.latitude[idx, None], shape)
    lon = np.broadcast_to(rays.longitude[idx, None], shape)
    corners = []
    for offset in (-half, half):
        bearing = np.broadcast_to((azimuth+offset)[:, None], shape)
        xlon, ylat, _ = geod.fwd(lon, lat, bearing, distance)
        x, y = merc.transform(xlon, ylat)
        corners.append((x, y))
    xmin = min(float(x.min()) for x, y in corners)
    xmax = max(float(x.max()) for x, y in corners)
    ymin = min(float(y.min()) for x, y in corners)
    ymax = max(float(y.max()) for x, y in corners)
    # Square Mercator pixels; small margin avoids clipping edge strokes.
    span = max(xmax-xmin, ymax-ymin, 1)*1.02
    cx, cy = (xmin+xmax)/2, (ymin+ymax)/2
    xmin, xmax, ymin, ymax = cx-span/2, cx+span/2, cy-span/2, cy+span/2
    pixels = [((x-xmin)/span*(grid_size-1), (ymax-y)/span*(grid_size-1)) for x, y in corners]
    data = np.ma.masked_invalid(rays.fields[field]['data'][idx])
    from ui.map.radar_overlay import NWS_VEL_CMAP
    from matplotlib import colormaps
    from matplotlib.colors import Normalize
    if style is not None:
        from ui.map.radar_overlay import COLORMAPS
        lo, hi, cmap = style['vmin'], style['vmax'], COLORMAPS[style['colormap']]
    elif 'vel' in field.lower():
        lo, hi, cmap = -30.0, 30.0, NWS_VEL_CMAP
    else:
        finite = data.compressed()
        lo, hi = np.percentile(finite, [2, 98]) if len(finite) else (0, 1)
        if hi <= lo:
            hi = lo + 1
        cmap = colormaps['viridis']
    colors = cmap(Normalize(lo, hi)(data), bytes=True)
    image = Image.new('RGBA', (grid_size, grid_size))
    draw = ImageDraw.Draw(image)
    left, right = pixels
    for i, j in zip(*np.nonzero(~np.ma.getmaskarray(data))):
        color = tuple(int(v) for v in colors[i, j])
        if half == 0:
            draw.line([(left[0][i,j],left[1][i,j]),(left[0][i,j+1],left[1][i,j+1])], fill=color, width=1)
        else:
            draw.polygon([(left[0][i,j],left[1][i,j]),(left[0][i,j+1],left[1][i,j+1]),
                          (right[0][i,j+1],right[1][i,j+1]),(right[0][i,j],right[1][i,j])], fill=color)
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    west, south = inv.transform(xmin, ymin)
    east, north = inv.transform(xmax, ymax)
    return stream.getvalue(), [west, south, east, north], {
        'start': datetime.fromtimestamp(float(rays.time_epoch[idx[0]]), timezone.utc).isoformat(),
        'end': datetime.fromtimestamp(float(rays.time_epoch[idx[-1]]), timezone.utc).isoformat(),
        'rays': len(idx), 'field': field, 'units': rays.fields[field].get('units',''),
        'vmin': float(lo), 'vmax': float(hi), 'azimuth_reference': 'file-declared north',
        'additional_velocity_motion_correction': False,
    }


