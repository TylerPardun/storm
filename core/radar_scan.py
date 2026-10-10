
from datetime import datetime


# radar scan record
class RadarScan:
    # create a new radar scan
    def __init__(
        self,
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
        az_offset=0.0,
    ):
        # assign site id
        self.site = site
        # assign product code
        self.product = product
        # assign scan time
        self.scan_time = scan_time
        # assign data array
        self.data = data
        # latitude / longitude of every gate (may be None when a subclass
        # computes them on first use, see _compute_latlon)
        self._lats = lats
        self._lons = lons
        # assign display min
        self.vmin = vmin
        # assign display max
        self.vmax = vmax
        # assign units label
        self.units = units
        # assign colormap name
        self.colormap = colormap
        # assign azimuth offset
        self.az_offset = az_offset

    # gate positions; computed on first use when not given up front
    @property
    def lats(self):
        if self._lats is None:
            self._lats, self._lons = self._compute_latlon()
        return self._lats

    @lats.setter
    def lats(self, value):
        self._lats = value

    @property
    def lons(self):
        if self._lons is None:
            self._lats, self._lons = self._compute_latlon()
        return self._lons

    @lons.setter
    def lons(self, value):
        self._lons = value

    def _compute_latlon(self):
        raise ValueError("this scan has no gate positions")

    # what the renderer needs, without requiring every gate's position:
    # the first gate of each ray (radar center), rays to measure gate
    # spacing from, and the gates that bound the scan. A scan holding full
    # arrays answers from them; one computing positions lazily
    # (Level2RadarScan from the archive) computes only these.
    def first_ring(self):
        return self.lats[:, 0], self.lons[:, 0]

    def spacing_rays(self):
        return self.lats, self.lons

    def extent_points(self):
        return self.lats, self.lons

    # how many seconds old this scan is
    @property
    def age_seconds(self):
        # local import to avoid global timezone import
        from datetime import timezone
        # compute age
        return (datetime.now(timezone.utc) - self.scan_time).total_seconds()

    # human-readable label for ui display
    @property
    def label(self):
        # get friendly product name
        name = PRODUCT_META.get(self.product, {}).get("name", self.product)
        # return label
        return f"{self.site} {name} {self.scan_time.strftime('%H:%M')}Z"



PRODUCT_META = {
    "N0B": {
        "name":     "Base Reflectivity (SR)",
        "units":    "dBZ",
        "vmin":     -32.0,
        "vmax":     90.0,
        "colormap": "nws_ref",
    },
    "N0Q": {
        "name":     "Base Reflectivity",
        "units":    "dBZ",
        "vmin":     -32.0,
        "vmax":     90.0,
        "colormap": "nws_ref",
    },
    "N0U": {
        "name":     "Base Velocity",
        "units":    "kt",
        "vmin":     -75.0,
        "vmax":      75.0,
        "colormap": "nws_vel",
    },
    # Super-res base velocity (alternate THREDDS product code — same format as N0U)
    "NBU": {
        "name":     "Base Velocity",
        "units":    "kt",
        "vmin":     -75.0,
        "vmax":      75.0,
        "colormap": "nws_vel",
    },
    # Legacy 16-level storm-relative velocity fallback (kt, ±64 kt range)
    "N0S": {
        "name":     "Storm Relative Velocity",
        "units":    "kt",
        "vmin":     -75.0,
        "vmax":      75.0,
        "colormap": "nws_vel",
    },
    "N0C": {
        "name":     "Correlation Coefficient",
        "units":    "",
        "vmin":     0.0,
        "vmax":     1.0,
        "colormap": "nws_cc",
    },
    "N0X": {
        "name":     "Diff. Reflectivity",
        "units":    "dB",
        "vmin":     -4.0,
        "vmax":      8.0,
        "colormap": "nws_zdr",
    },
    "N0K": {
        "name":     "Specific Diff. Phase",
        "units":    "deg/km",
        "vmin":     -2.0,
        "vmax":     10.0,
        "colormap": "nws_kdp",
    },
}
