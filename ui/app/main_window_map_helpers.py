
from datetime import datetime, timezone

from ui.widgets.layer_order_pill import MAPLIBRE_LAYERS
from ui.map.widget import TILES_PATH
from ui.controls.radar_controls import NEXRAD_SITES


class MainWindowMapHelpersMixin:

    def _show_monitor_mode_status(self):
        self.status_msg_label.setText(
            "  Monitor mode — no local obs data"
        )
        self.status_msg_label.setStyleSheet(
            "color: #4A9EFF; font-size: 10px; font-weight: 600; letter-spacing: 0.5px;"
        )
        self._layout_overlays()

    def _load_radar_station_sites(self) -> list[dict]:
        bounds = self._load_mbtiles_bounds()
        sites: list[dict] = []
        for site_id, name, lat, lon in NEXRAD_SITES:
            if bounds and not self._point_in_bounds(lat, lon, bounds):
                continue
            sites.append({
                "site_id": site_id,
                "name": name,
                "lat": lat,
                "lon": lon,
            })
        return sites

    def _nearest_radar_site(self, lat: float, lon: float) -> str:
        candidates = self._radar_station_sites or [
            {"site_id": site_id, "lat": site_lat, "lon": site_lon}
            for site_id, _, site_lat, site_lon in NEXRAD_SITES
        ]
        nearest = min(
            candidates,
            key=lambda site: self._haversine_km(lat, lon, site["lat"], site["lon"]),
        )
        return nearest["site_id"]

    def _load_mbtiles_bounds(self) -> tuple[float, float, float, float] | None:
        from core import mbtiles      # read-only: never creates a missing file
        return mbtiles.bounds(TILES_PATH)

    @staticmethod
    def _point_in_bounds(
        lat: float,
        lon: float,
        bounds: tuple[float, float, float, float],
    ) -> bool:
        west, south, east, north = bounds
        return west <= lon <= east and south <= lat <= north

    @staticmethod
    def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        import math

        r = 6371.0
        lat1r = math.radians(lat1)
        lat2r = math.radians(lat2)
        dlat = lat2r - lat1r
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return r * c


    def _update_clock(self):
        now = datetime.now(timezone.utc)
        self.clock_label.setText(now.strftime("%H:%M:%S UTC"))
        self.date_label.setText(f"{now.day} {now.strftime('%b %Y')}")
        hidden_vehicle_ids: list[str] = []
        vehicle_panel_needs_rebuild = False
        vehicle_label_only_updates: dict[str, str] = {}
        vehicle_detail_needs_refresh = False
        for v in list(self._vehicles.values()):
            obs = v.latest_obs
            if obs is None:
                continue
            if not self._should_display_vehicle_obs(obs):
                hidden_vehicle_ids.append(v.id)
                continue
            color = self._obs_age_color(obs)
            age_label = self._obs_age_label(obs)
            prev_state = self._vehicle_age_display_state.get(v.id)
            if prev_state == (color, age_label):
                continue
            prev_color = prev_state[0] if prev_state else None
            self._vehicle_age_display_state[v.id] = (color, age_label)
            self.map_widget.add_vehicle(v.id, v.lat, v.lon, color, v.icon_type)
            if prev_color != color:
                # color tier changed → sort order may change → full rebuild needed.
                vehicle_panel_needs_rebuild = True
            else:
                vehicle_label_only_updates[v.id] = age_label
            if v.id in self._selected_vehicle_ids:
                vehicle_detail_needs_refresh = True

        for vehicle_id in hidden_vehicle_ids:
            self._hide_vehicle(vehicle_id)

        if vehicle_panel_needs_rebuild:
            self._refresh_vehicle_panel()
        elif vehicle_label_only_updates:
            for vid, age_label in vehicle_label_only_updates.items():
                lbl = self._vehicle_row_age_labels.get(vid)
                if lbl is not None:
                    lbl.setText(f"{age_label} old")
        if vehicle_detail_needs_refresh:
            self._refresh_vehicle_detail()
        if not self._clock_layout_synced:
            self._layout_overlays()
            self._clock_layout_synced = True


    # Symbol layers from the base map style (place names, road labels) that
    # must always render above any user-reorderable data layer. Listed
    # bottom→top in their original style order so the final z-order matches
    # the static stylesheet after we promote them.
    _ALWAYS_ON_TOP_LAYERS: tuple[str, ...] = (
        "road-label-motorway",
        "road-label-primary",
        "road-label-tertiary",
        "road-label-minor",
        "state-label",
        "place-city",
        "place-village",
    )

    def _apply_layer_order(self, order: list[str]) -> None:
        """Reorder MapLibre layers to match the confirmed layer stack (bottom → top)."""
        # walk bottom→top.  For each group, move every layer in the group
        def _first_ml_id(key: str) -> str | None:
            for lid in MAPLIBRE_LAYERS.get(key, []):
                return lid
            return None

        for i, key in enumerate(order):
            ml_ids = MAPLIBRE_LAYERS.get(key, [])
            if not ml_ids:
                continue
            # find the before-anchor: first MapLibre ID of the next group above
            before = None
            for j in range(i + 1, len(order)):
                before = _first_ml_id(order[j])
                if before:
                    break
            for lid in ml_ids:
                self.map_widget.move_layer_before(lid, before)

        # Promote place-name / road-label symbol layers to the very top so
        # data layers (radar, satellite, etc.) can never bury them, regardless
        # of the user's chosen order.
        for lid in self._ALWAYS_ON_TOP_LAYERS:
            self.map_widget.move_layer_before(lid, None)

    def _set_layer_active(self, key: str, active: bool) -> None:
        """Notify the layer pill that a layer's visibility changed."""
        if hasattr(self, "_layer_pill"):
            self._layer_pill.set_layer_active(key, active)

    def _refresh_annotation_layer(self) -> None:
        """Update the annotations pill entry based on whether any annotations or drawings exist."""
        has_content = bool(
            getattr(self, "_annotations", None) or getattr(self, "_drawings", None)
        )
        self._set_layer_active("annotations", has_content)

    def _refresh_storm_cone_layer(self) -> None:
        """Update the storm_cones pill entry based on whether any cones exist."""
        self._set_layer_active("storm_cones", bool(getattr(self, "_storm_cones", None)))
