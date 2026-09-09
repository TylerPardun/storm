from core.radar_scan import RadarScan


# Native field names and units confirmed against real downloaded NOXP files
# (Sigmet 2013-05-31, CfRadial 2022-09-28 -- see
# planning/noxp-adapter-review.md and case_data/evidence/noxp-adapter-validation-20260909.json).
# file_field_names=True keeps these as-is; there is no pyart standard-name
# remapping to fall back on. Unrecognized fields (a format/version this
# table hasn't seen) get _GENERIC_FIELD_META rather than raising.
NOXP_FIELDS: dict[str, dict] = {
    "DBT":      {"label": "Total Power (DBT)",        "units": "dBZ", "vmin": -32.0, "vmax": 90.0, "colormap": "nws_ref"},
    "DBZ":      {"label": "Reflectivity (DBZ)",        "units": "dBZ", "vmin": -32.0, "vmax": 90.0, "colormap": "nws_ref"},
    "DBZ_TOT":  {"label": "Total Reflectivity (DBZ_TOT)", "units": "dBZ", "vmin": -32.0, "vmax": 90.0, "colormap": "nws_ref"},
    "VEL":      {"label": "Velocity (VEL)",             "units": "m/s",  "vmin": -40.0, "vmax": 40.0, "colormap": "nws_vel"},
    "WIDTH":    {"label": "Spectrum Width (WIDTH)",     "units": "m/s",  "vmin": 0.0,   "vmax": 15.0, "colormap": "nws_sw"},
    "ZDR":      {"label": "Diff. Reflectivity (ZDR)",   "units": "dB",   "vmin": -4.0,  "vmax": 8.0,  "colormap": "nws_zdr"},
    "KDP":      {"label": "Specific Diff. Phase (KDP)", "units": "deg/km", "vmin": -2.0, "vmax": 10.0, "colormap": "nws_kdp"},
    "PHIDP":    {"label": "Differential Phase (PHIDP)", "units": "deg",  "vmin": 0.0,   "vmax": 360.0, "colormap": "nws_phi"},
    "SQI":      {"label": "Signal Quality (SQI)",       "units": "",     "vmin": 0.0,   "vmax": 1.0,  "colormap": "nws_cc"},
    "RHOHV":    {"label": "Corr. Coefficient (RHOHV)",  "units": "",     "vmin": 0.0,   "vmax": 1.0,  "colormap": "nws_cc"},
}

_GENERIC_FIELD_META = {"label": None, "units": "", "vmin": -1.0, "vmax": 1.0, "colormap": "nws_ref"}


def field_meta(field_name: str) -> dict:
    meta = NOXP_FIELDS.get(field_name, _GENERIC_FIELD_META)
    return meta if meta["label"] is not None else {**meta, "label": field_name}


class NoxpRadarScan(RadarScan):
    """A RadarScan produced from one sweep of a NOXP mobile-radar volume.

    Extra attributes
    ----------------
    sweep_index : int
        Index of this sweep within the parent RadarVolume.
    elevation_deg : float
        Mean elevation angle of this sweep (NaN if unavailable).
    available_sweeps : list[tuple[int, float]]
        (index, mean elevation_deg) for every sweep in the parent volume.
    native_field : str
        The NOXP native field name that was rendered (e.g. "DBZ").
    """

    def __init__(
        self,
        *,
        site,
        product,
        scan_time,
        data,
        lats,
        lons,
        vmin,
        vmax,
        units,
        colormap,
        sweep_index: int,
        elevation_deg: float,
        available_sweeps: list,
        native_field: str,
        az_offset: float = 0.0,
    ):
        super().__init__(
            site=site, product=product, scan_time=scan_time, data=data,
            lats=lats, lons=lons, vmin=vmin, vmax=vmax, units=units,
            colormap=colormap, az_offset=az_offset,
        )
        self.sweep_index = sweep_index
        self.elevation_deg = elevation_deg
        self.available_sweeps = available_sweeps
        self.native_field = native_field

    @property
    def label(self) -> str:
        meta = field_meta(self.native_field)
        return f"{self.site} {meta['label']} {self.elevation_deg:.1f}° {self.scan_time.strftime('%H:%M')}Z"
