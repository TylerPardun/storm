
import os
import ssl
from pathlib import Path

# determine the parent directory for the application
_PROJ = Path(__file__).parent

# determine the path for the aws credentials
_AWS_CANDIDATES = (_PROJ / "aws", _PROJ / ".aws")

# get the files for the aws credentials
_AWS = next((p for p in _AWS_CANDIDATES if p.exists()), _AWS_CANDIDATES[0])

# pull the application version
VERSION: str = (_PROJ / "VERSION").read_text().strip()

# set a default vehicle ID (will be overwritten)
VEHICLE_ID = "storm"

# set a default vehicle icon type (will be overwritten from launch dialog)
VEHICLE_ICON = "car"

# define the path to the obs file (will be overwritten)
OBS_FILE_DIR = ""
OBS_FILE_GPS_MODE = False   # True → GPS Ka column layout instead of FOFS

# define the interval to poll the obs file (local GUI update rate)
OBS_FILE_POLL_S = 1

# how often local obs are published to MQTT for other vehicles to see
OBS_MQTT_PUBLISH_S = 5

# column header names for real-time obs file
OBS_FILE_COL_LAT = "lat"
OBS_FILE_COL_LON = "lon"
OBS_FILE_COL_DATE = "gps_date"
OBS_FILE_COL_TIME = "gps_time"
OBS_FILE_COL_TEMP = "t_fast"
OBS_FILE_COL_DEWP = "dewpoint"
OBS_FILE_COL_WSPD = "sfc_wspd"
OBS_FILE_COL_WDIR = "sfc_wdir"
OBS_FILE_COL_PRES = "pressure"

# gps baud rate
GPS_BAUD = 4800

# path to previous deployment locations
DEPLOY_LOCS_FILE = str(_PROJ / "locs" / "deployment_locations.csv")

# pbkdf2-HMAC-SHA256 hashes (600 000 iterations) of the passphrases required
VEHICLE_PASSPHRASE_HASH = "CeNUNJ5o6dIu9Jgi3CKTIw==:E70QJVtj0v5u91wuOJEQJPDG+CJML30lCW+2BqzcLhM="
MONITOR_PASSPHRASE_HASH = "AYxyUvyUW9hzxWA7UMtNfA==:Bi6XDsi3+E52eLhEP7sT80Effgm3l6mAeKCEpNGNdqY="
ARCHIVE_PASSPHRASE_HASH = "O3uHJWRbpiJXzOOqTNrGLA==:LecEzsMFUFlijFzJpTlz8PaWGtxuTVx+l0ShiguqoVE="
ADMIN_PASSPHRASE_HASH = "N44K/AWhoT+O08E33gdLvg==:EbVnqJ4r5oRxSR2BjtxlRwpUjJOJoUe0oiIfFJQNUbM="

# accent color
ACCENT_COLOR = "#00CFFF"

# openRouteService API key (used by routing_fetcher.py)
ORS_API_KEY = os.environ.get(
    "ORS_API_KEY",
    "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjlhZTYwOTkzMzcwMTRlYjg5YTcxMjlkYmU0MGI1NTRmIiwiaCI6Im11cm11cjY0In0=",
)

# Shared NSSL API settings for authenticated data endpoints.
NSSL_API_ROOT = os.environ.get(
    "NSSL_API_ROOT",
    "https://api.nssl.noaa.gov",
).rstrip("/")
NSSL_API_KEY = os.environ.get(
    "NSSL_API_KEY",
    "bde39a474528766ea566fa21d7a0b89a8fb72a0391aa76a6bca6c26b88652aa0",
)

# api.nssl.noaa.gov serves its TLS chain without the Sectigo intermediate CA.
# macOS/curl paper over this via AIA fetching, but Python's ssl module does
# not, so every NSSL API caller should use this context, which trusts the
# bundled intermediate explicitly.
_NSSL_INTERMEDIATE_CERT = _PROJ / "certs" / "sectigo_public_server_authentication_ca_dv_r36.pem"


def _build_nssl_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if _NSSL_INTERMEDIATE_CERT.is_file():
        try:
            ctx.load_verify_locations(cafile=str(_NSSL_INTERMEDIATE_CERT))
        except ssl.SSLError:
            pass
    return ctx


NSSL_SSL_CONTEXT = _build_nssl_ssl_context()

# SFCOA mesoanalysis API base URL.
SFCOA_BASE_URL = os.environ.get(
    "SFCOA_BASE_URL",
    f"{NSSL_API_ROOT}/data/sfcoa",
)

# home location fallback
HOME_LAT, HOME_LON = 35.22, -97.44   # Norman, OK

# mqtt endpoint
MQTT_HOST = "a38pz70mp8mr8r-ats.iot.us-east-2.amazonaws.com"

# mqtt port
MQTT_PORT = 8883

# mqtt use tls boolean
MQTT_USE_TLS = True

# paths to certificates
MQTT_CA_CERT = str(_AWS / "storm.pem")
MQTT_CERT_FILE = str(_AWS / "storm.pem.crt")
MQTT_KEY_FILE = str(_AWS / "storm-private.pem.key")
