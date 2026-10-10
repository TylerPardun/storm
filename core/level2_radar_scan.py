
from core.radar_scan import RadarScan



L2_PRODUCTS: dict[str, dict] = {
    "reflectivity": {
        "label":    "Reflectivity (REF)",
        "units":    "dBZ",
        "vmin":     -32.0,
        "vmax":      90.0,
        "colormap": "nws_ref",
    },
    "velocity": {
        "label":    "Velocity (VEL)",
        "units":    "kt",
        "vmin":     -75.0,
        "vmax":      75.0,
        "colormap": "nws_vel",
    },
    "cross_correlation_ratio": {
        "label":    "Corr. Coefficient (CC)",
        "units":    "",
        "vmin":      0.0,
        "vmax":      1.0,
        "colormap": "nws_cc",
    },
    "differential_reflectivity": {
        "label":    "Diff. Reflectivity (ZDR)",
        "units":    "dB",
        "vmin":     -4.0,
        "vmax":      8.0,
        "colormap": "nws_zdr",
    },
    "spectrum_width": {
        "label":    "Spectrum Width (SW)",
        "units":    "kt",
        "vmin":      0.0,
        "vmax":     30.0,
        "colormap": "nws_sw",
    },
    "differential_phase": {
        "label":    "Differential Phase (PHI)",
        "units":    "deg",
        "vmin":      0.0,
        "vmax":    360.0,
        "colormap": "nws_phi",
    },
    "specific_differential_phase": {
        "label":    "Specific Diff. Phase (KDP)",
        "units":    "deg/km",
        "vmin":     -2.0,
        "vmax":     10.0,
        "colormap": "nws_kdp",
    },
    "clutter_filter_power_removed": {
        "label":    "Clutter Filter Power Removed (CFP)",
        "units":    "dB",
        "vmin":      0.0,
        "vmax":     70.0,
        "colormap": "nws_cfp",
    },
}

# default product shown when the archive session opens.
DEFAULT_L2_PRODUCT = "reflectivity"


class Level2RadarScan(RadarScan):
    """
    A RadarScan produced from a NEXRAD Level-2 archive file.

    Extra attributes
    ----------------
    tilt_deg : float
        Elevation angle of this sweep in degrees.
    available_tilts : list[float]
        All elevation angles present in the parent Level-2 file (sorted).
    available_products : list[str]
        pyart field names present in the sweep (subset of L2_PRODUCTS keys).
    pyart_field : str
        The pyart field name that was rendered (e.g. "reflectivity").
    """

    def __init__(
        self,
        site,
        product,       # human-readable label, e.g. "REF"
        scan_time,
        data,
        lats,
        lons,
        vmin,
        vmax,
        units,
        colormap,
        tilt_deg: float,
        available_tilts: list,
        available_products: list,
        pyart_field: str,
        az_offset: float = 0.0,
        gate_geometry=None,
    ):
        """gate_geometry: (radar_lat, radar_lon, azimuths_deg, ranges_km) --
        given instead of lats/lons, gate positions are computed only where
        needed (see RadarScan.first_ring etc.); the full grids only if
        something asks for scan.lats / scan.lons."""
        self._geometry = gate_geometry
        super().__init__(
            site=site,
            product=product,
            scan_time=scan_time,
            data=data,
            lats=lats,
            lons=lons,
            vmin=vmin,
            vmax=vmax,
            units=units,
            colormap=colormap,
            az_offset=az_offset,
        )
        self.tilt_deg = tilt_deg
        self.available_tilts = sorted(available_tilts)
        self.available_products = available_products
        self.pyart_field = pyart_field

    # every Nth ray is enough to measure gate spacing (a median over rays)
    SPACING_RAY_STEP = 10

    def _latlon(self, ray_index, gate_index, dtype=None):
        """Positions (float32, as the full grids are stored, unless dtype is
        given) of the given rays x gates, by the same WGS84
        azimuthal-equidistant placement."""
        import numpy as np
        from pyproj import Proj, Transformer
        radar_lat, radar_lon, azimuths, ranges = self._geometry
        az = np.deg2rad(np.asarray(azimuths, dtype=np.float64)[ray_index])
        rng = np.asarray(ranges, dtype=np.float64)[gate_index]
        x_km, y_km = np.outer(np.sin(az), rng), np.outer(np.cos(az), rng)
        aeqd = Proj(proj="aeqd", lat_0=radar_lat, lon_0=radar_lon, datum="WGS84", units="km")
        lons, lats = Transformer.from_proj(aeqd, Proj("epsg:4326"), always_xy=True).transform(x_km, y_km)
        return lats.astype(dtype or np.float32), lons.astype(dtype or np.float32)

    def _compute_latlon(self):
        if self._geometry is None:
            return super()._compute_latlon()
        return self._latlon(slice(None), slice(None))

    def _lazy(self) -> bool:
        return self._geometry is not None and self._lats is None

    def first_ring(self):
        if not self._lazy():
            return super().first_ring()
        lats, lons = self._latlon(slice(None), slice(0, 1))
        return lats[:, 0], lons[:, 0]

    def spacing_rays(self):
        if not self._lazy():
            return super().spacing_rays()
        # full precision: float32 positions (~0.1 m) put a few mm of noise on
        # the measured gate width, which adds up over a thousand gates
        import numpy as np
        return self._latlon(slice(None, None, self.SPACING_RAY_STEP), slice(None), np.float64)

    def extent_points(self):
        """The first and last gate of every ray: along each ray latitude and
        longitude change monotonically, so the scan's bounds lie on these."""
        if not self._lazy():
            return super().extent_points()
        return self._latlon(slice(None), [0, -1])

    @property
    def label(self) -> str:
        meta = L2_PRODUCTS.get(self.pyart_field, {})
        name = meta.get("label", self.pyart_field)
        return f"{self.site} {name} {self.tilt_deg:.1f}° {self.scan_time.strftime('%H:%M')}Z"
