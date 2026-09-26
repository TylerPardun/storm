
import os
import re
import sys
import uuid
import socket
import config
import logging
import net_compat
import argparse
import faulthandler
import runtime_flags
from dataclasses import replace
from datetime import datetime, timezone
from ui.launch.dialog import LaunchDialog
from ui.map.radar_overlay import set_render_grid_size, set_adaptive_render_grid
from data.ingest.truck_replay import load_truck_observations

# try to import PyQt packages
try:
    from PyQt6.QtCore import QCoreApplication, QTimer, Qt
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox
# if there is a failure, show an error message
except ModuleNotFoundError as exc:
    if exc.name == "PyQt6":
        print("PyQt6 is not installed, please activate the 'storm' environment, then run 'python main.py'.", file=sys.stderr)

        # exit with error
        sys.exit(1)
    raise

# reference kept alive so the OS doesn't release the bound port
_instance_lock_file = None
# kept alive so the OS doesn't GC the handle before a fault occurs
_fault_log_file = None


def _acquire_instance_lock() -> bool:
    """Use an exclusive lockfile as a single-instance guard.

    Returns True if this is the first instance; False if one is already running.
    The file handle is kept alive in _instance_lock_file for the entire process
    lifetime — closing it would release the lock. The OS releases the lock
    automatically on crash, so no stale lockfiles accumulate.
    """
    global _instance_lock_file
    import tempfile  # noqa: PLC0415

    lock_path = os.path.join(tempfile.gettempdir(), "storm_instance.lock")

    try:
        fh = open(lock_path, "w")
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _instance_lock_file = fh  # keep alive to hold the lock
        return True
    except OSError:
        return False


# function to parse command line arguments (if run from the command line)
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="STORM - Severe Thunderstorm Observation and Reconnaissance Monitor",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Debug-run profiles:\n"
            "  0 normal\n"
            "  1 safe map mode only\n"
            "  2 runtime-safe + safe map mode\n"
            "  3 disable radar path\n"
            "  4 disable MQTT path\n"
            "  5 minimal/offline diagnostic shell\n"
            "  6 MQTT diagnostics (no TLS)\n\n"
            "Examples:\n"
            "  python main.py --debug-run 2\n"
            "  python main.py --disable-mqtt --disable-radar\n"
            "  python main.py --debug --disable-mqtt"
        ),
    )

    # run in debug mode 
    parser.add_argument("--debug", action="store_true", help="enable debug logging and in-app debug panel")

    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="logging verbosity (default: WARNING; --debug overrides to DEBUG)",
    )

    # mode to test a local file as "real-time"
    parser.add_argument(
        "--truck-replay-file",
        default="",
        help="path to CSV/TXT truck data file to replay locally (no MQTT)",
    )

    # interval to read in local test data (ms)
    parser.add_argument(
        "--truck-replay-interval-ms",
        type=int,
        default=1000,
        help="milliseconds between replayed truck samples (default: 1000)",
    )

    # shifts local test data to real-time
    parser.add_argument(
        "--truck-replay-restamp",
        action="store_true",
        help=(
            "shift replay timestamps so the last obs lands at now; useful for old files so "
            "history/freshness behavior matches live data"
        ),
    )

    # render resolution for radar data
    parser.add_argument(
        "--render-grid-size",
        type=int,
        default=0,
        metavar="N",
        help=(
            "radar render resolution override (default runtime value: adaptive, starts at 768). "
            "Suggested values: 1536, 1024, 768, 512, 384, 256, 128"
        ),
    )

    # debug mode (NOTE: there are different flags here that can be seen in the --help section)
    parser.add_argument(
        "--debug-run",
        type=int,
        choices=range(0, 7),
        default=0,
        metavar="N",
        help="quick diagnostic profile number (see 'Debug-run profiles' below)",
    )

    # actions for the debug-run flag
    parser.add_argument("--enable-startup-toggles", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-radar", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-mqtt", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-annotations", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-deploy-locs", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-data-inputs", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--mqtt-no-tls", action=argparse.BooleanOptionalAction, default=None)

    # return the parsed arguments
    return parser

# function to register the custom storm:// URL scheme with WebEngine.
def _register_storm_scheme() -> None:
    try:
        from PyQt6.QtWebEngineCore import QWebEngineUrlScheme
        scheme = QWebEngineUrlScheme(b"storm")
        scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
        flags = (
            QWebEngineUrlScheme.Flag.SecureScheme
            | QWebEngineUrlScheme.Flag.CorsEnabled
        )
        # fetchApiAllowed lets JS fetch() use the storm:// scheme (Qt 6.2+)
        fetch_flag = getattr(QWebEngineUrlScheme.Flag, "FetchApiAllowed", None)
        if fetch_flag is not None:
            flags |= fetch_flag
        scheme.setFlags(flags)
        QWebEngineUrlScheme.registerScheme(scheme)
    except Exception as exc:
        print(f"[STORM] WARNING: could not register storm:// scheme: {exc}", flush=True)


# function to configure qt webengine
def _configure_qt_webengine_env() -> None:
    # if the platform is windows, force software rendering
    if sys.platform == "win32":
        runtime_safe = runtime_flags.FLAGS.runtime_safe
        if runtime_safe:
            os.environ["QMLSCENE_DEVICE"] = "softwarecontext"
            os.environ["QT_QUICK_BACKEND"] = "software"
            os.environ["QT_OPENGL"] = "software"
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu --disable-gpu-compositing"
        else:
            os.environ.pop("QMLSCENE_DEVICE", None)
            os.environ.pop("QT_QUICK_BACKEND", None)
            os.environ.pop("QT_OPENGL", None)
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --in-process-gpu --ignore-gpu-blocklist"
    # otherwise, use mac/linux defaults
    else:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --in-process-gpu"


def _configure_qt_application_attributes() -> None:
    """Set Qt attributes that must be configured before QApplication exists."""
    if runtime_flags.FLAGS.safe_map_mode:
        return
    QCoreApplication.setAttribute(
        Qt.ApplicationAttribute.AA_ShareOpenGLContexts,
        True,
    )

# function to generate a default vehicle ID (if in monitor mode)
def _default_vehicle_id() -> str:
    # pull the host name
    host = (socket.gethostname() or "").strip().lower()

    # additional formatting for the host name
    host = re.sub(r"[^a-z0-9-]+", "-", host).strip("-")

    # derive a stable 4-char hex suffix from the MAC address so two machines
    mac_suffix = format(uuid.getnode() & 0xFFFF, "04x")

    # if the host name is empty, use "device"
    if not host:
        host = "device"

    # return the ID used for the MQTT connection
    return f"storm-{host}-{mac_suffix}"

# function to normalize a user-provided vehicle ID
def _normalize_vehicle_id(raw: str) -> str:
    # strip leading/trailing whitespace
    vid = (raw or "").strip().lower()

    # remove non-alphanumeric characters
    vid = re.sub(r"[^a-z0-9-]+", "-", vid).strip("-")

    # if the ID is left empty, use the default
    if not vid or vid == "storm":
        # run the default function
        return _default_vehicle_id()
    
    # return the normalized ID
    return vid

# function to configure logging
def _configure_logging(level_name: str) -> None:
    # set the logging level
    level = getattr(logging, level_name.upper(), logging.WARNING)

    # configure logging: every line carries the UTC date/time, this run's
    # session ID and the open case (see log_context)
    import log_context
    logging.basicConfig(level=level)
    for handler in logging.getLogger().handlers:
        log_context.install(handler)

    # write WARNING+ logs to a persistent file for post-session review
    try:
        from logging.handlers import RotatingFileHandler
        _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storm_errors.log")
        _fh = RotatingFileHandler(
            _log_path, mode="a", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8",
        )
        _fh.setLevel(logging.WARNING)
        log_context.install(_fh)
        logging.getLogger().addHandler(_fh)
    except Exception:
        pass

    # set logging levels (if the level is greater than DEBUG)
    if level > logging.DEBUG:
        logging.getLogger("matplotlib").setLevel(logging.WARNING)
        logging.getLogger("metpy").setLevel(logging.WARNING)

# function to check for missing required files and show user-visible warnings
def _warn_missing_files() -> None:
    """Show QMessageBox warnings for any missing required files.

    Called after QApplication is created but before the main window is built, so
    dialogs render correctly.  Warnings are non-fatal — the app continues in a
    degraded state so the user can at least see what is wrong.
    """
    tiles_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "tiles", "storm.mbtiles")
    )
    if not os.path.exists(tiles_path):
        QMessageBox.warning(
            None,
            "Missing Map Tiles",
            f"Map tiles file not found:\n\n  {tiles_path}\n\n"
            "The map will not render correctly.\n"
            "Ensure 'tiles/storm.mbtiles' is present in the application directory.",
        )

    if runtime_flags.FLAGS.disable_mqtt:
        return

    aws_dir = os.path.dirname(config.MQTT_CA_CERT)
    cert_files = [config.MQTT_CA_CERT, config.MQTT_CERT_FILE, config.MQTT_KEY_FILE]

    if not os.path.isdir(aws_dir):
        QMessageBox.warning(
            None,
            "Missing AWS Credentials Folder",
            f"The AWS credentials directory was not found:\n\n  {aws_dir}\n\n"
            "MQTT features (annotation sync, vehicle tracking) will be unavailable.\n"
            "Create the 'aws/' folder and add your certificates to enable them.",
        )
    else:
        missing = [f for f in cert_files if not os.path.isfile(f)]
        if missing:
            names = "\n".join(f"  \u2022 {os.path.basename(f)}" for f in missing)
            QMessageBox.warning(
                None,
                "Missing AWS Certificate Files",
                f"The following MQTT certificate files were not found:\n\n{names}\n\n"
                "MQTT features (annotation sync, vehicle tracking) will be unavailable.",
            )




# main function
def main() -> None:
    global _fault_log_file
    # try to configure fault handler
    try:
        # open the log file and keep it alive at module level so GC doesn't close it
        _fault_log_file = open("storm_fault.log", "a", buffering=1, encoding="utf-8")

        # enable the fault handler
        faulthandler.enable(file=_fault_log_file, all_threads=True)
    # if that fails
    except Exception:
        # do nothing
        pass

    # stop every fetch waiting on unreachable IPv6 addresses (see net_compat)
    net_compat.prefer_ipv4()

    # build the argument parser
    parser = _build_parser()

    # parse the arguments
    args = parser.parse_args()

    # configure runtime flags
    runtime_flags.reset_flags()

    # apply debug run profile
    runtime_flags.apply_debug_run_profile(args.debug_run)

    # apply overrides
    runtime_flags.apply_overrides(
        enable_startup_toggles = args.enable_startup_toggles,
        disable_radar = args.disable_radar,
        disable_mqtt = args.disable_mqtt,
        disable_annotations = args.disable_annotations,
        disable_deploy_locs = args.disable_deploy_locs,
        disable_data_inputs = args.disable_data_inputs,
        mqtt_no_tls = args.mqtt_no_tls,
    )

    # finalize flags
    runtime_flags.finalize_flags()

    # if the debug run profile is enabled
    if args.debug_run > 0 and not args.debug:
        # set the debug flag
        args.debug = True

    # configure logging
    log_level = "DEBUG" if args.debug else args.log_level
    _configure_logging(log_level)

    # set the vehicle ID
    config.VEHICLE_ID = _normalize_vehicle_id(config.VEHICLE_ID)

    # configure the Qt webengine environment
    _configure_qt_webengine_env()

    # set Qt application attributes required before QApplication exists
    _configure_qt_application_attributes()

    # register storm:// URL scheme — must happen before QApplication
    if not runtime_flags.FLAGS.safe_map_mode:
        _register_storm_scheme()

    # set the render grid size for radar overlays
    if args.render_grid_size > 0:
        # set the render grid size
        set_render_grid_size(args.render_grid_size)

    # create the application
    app = QApplication(sys.argv)

    # set application properties
    app.setApplicationName("STORM")
    app.setOrganizationName("STORM")
    app.setFont(QFont("Segoe UI", 10))

    # set application quit behavior
    app.setQuitOnLastWindowClosed(False)

    if not _acquire_instance_lock():
        QMessageBox.warning(
            None,
            "STORM Already Running",
            "STORM is already running on this machine.\n\n"
            "Please close all existing STORM instances before opening a new one.",
        )
        sys.exit(0)


    from ui.app.main_window import MainWindow  # noqa: PLC0415

    # Session loop: normally runs once. Loops back to the launch dialog,
    # without tearing down the QApplication or process, when the user exits
    # an archive session via the DAY button (or aborts the loading dialog) —
    # see MainWindow.closeEvent / session_aborted. app.exec() is safe to
    # call repeatedly in sequence; each call blocks until the matching
    # app.quit() (ours, on session_aborted, or the real one on a normal quit).
    while True:
        # show the launch dialog for mode selection and password verification
        dialog = LaunchDialog()

        # if the dialog is not accepted
        if dialog.exec() != QDialog.DialogCode.Accepted:
            # exit
            sys.exit(0)

        # get the vehicle ID
        config.VEHICLE_ID = _normalize_vehicle_id(dialog.vehicle_id())

        # get the vehicle icon type selected by the user
        config.VEHICLE_ICON = dialog.vehicle_icon()

        # get the directory for real-time observation files (if any)
        config.OBS_FILE_DIR      = dialog.data_dir()
        config.OBS_FILE_GPS_MODE = dialog.gps_file_mode()

        # get mode from the dialog
        monitor      = dialog.monitor()
        viewer       = dialog.viewer()
        archive_time = dialog.archive_start_time()   # None unless archive mode
        runtime_flags.FLAGS.admin_mode = dialog.admin_mode()

        # apply radar render resolution from the launch dialog
        _dlg_res = dialog.radar_resolution()
        if _dlg_res > 0:
            set_render_grid_size(_dlg_res)
            set_adaptive_render_grid(False)

        _warn_missing_files()

        from ui.dialogs.loading_dialog import LoadingDialog  # noqa: PLC0415
        loading_dialog = LoadingDialog()
        loading_dialog.show()
        app.processEvents()  # Force the dialog to render immediately

        # create the main window
        window = MainWindow(
            debug=args.debug,
            monitor=monitor,
            viewer=viewer,
            archive_time=archive_time,
        )

        # close loading dialog once main window is ready
        loading_dialog.close()
        loading_dialog = None

        # define the JS console message handler
        js_log = logging.getLogger("storm.js")

        # if the map widget has a page
        if hasattr(window.map_widget, "page"):
            # define the JS console message handler
            def handle_js_message(level, message, line, source):
                js_log.debug("JS [%s:%s] %s", source, line, message)

            # set the JS console message handler
            window.map_widget.page().javaScriptConsoleMessage = handle_js_message

        window.show()

        # if a truck replay file is specified
        if args.truck_replay_file:
            # start the truck replay
            _start_truck_replay(window=window, file_path=args.truck_replay_file, interval_ms = max(50, args.truck_replay_interval_ms),
                                restamp=args.truck_replay_restamp)

        # session_aborted means "show the launch dialog again", not "quit" —
        # closeEvent() itself decides which one this run is.
        session_ending = {"restart": False}

        def _on_session_aborted():
            session_ending["restart"] = True
            app.quit()

        window.session_aborted.connect(_on_session_aborted)

        # run the application — blocks until app.quit() fires, whether from
        # a real quit (closeEvent's else-branch) or the handler above.
        app.exec()

        if not session_ending["restart"]:
            # real quit: exit exactly like the pre-session-loop code did,
            # with no explicit window cleanup. QtWebEngine requires its
            # QWebEngineView/Page to actually be destroyed (not just
            # deleteLater-scheduled) before the process tears down its
            # profile, or it logs "Release of profile requested but
            # WebEnginePage still not deleted" and can hang requiring a
            # force-quit. deleteLater() alone doesn't run here -- there's no
            # event loop left to process it before sys.exit -- so don't
            # call it on this path.
            sys.exit(0)

        # restarting: deleteLater() here is safe because the LaunchDialog's
        # own exec() below is a nested event loop that will actually flush
        # the deferred deletion before the next MainWindow gets built.
        window.deleteLater()

# function to start a truck replay
def _start_truck_replay(window, file_path: str, interval_ms: int, restamp: bool = False) -> None:
    # logger
    log = logging.getLogger("storm.replay")

    # try to load in observations
    try:
        observations = load_truck_observations(file_path)
    # error out if there are issues with the provided file path
    except Exception as exc:
        log.error("truck replay failed to load %s: %s", file_path, exc)
        return

    # if there are no observations
    if not observations:
        # log a warning
        log.warning("truck replay file has no valid rows: %s", file_path)
        return

    # if restamp of times was requested
    if restamp:
        # pull the current time
        now = datetime.now(timezone.utc)

        # restamp the observations
        shift = now - observations[-1].timestamp
        observations = [replace(o, timestamp=o.timestamp + shift) for o in observations]

        # log action
        log.info("restamped %d obs (shift=%.0fs)", len(observations), shift.total_seconds())

    # log the replay data loaded
    log.info("truck replay loaded %d rows from %s (interval=%dms, restamp=%s)", len(observations), file_path, interval_ms, restamp)

    # define the replay timer
    idx = 0

    # define the tick handler
    def tick():
        # pull the current index
        nonlocal idx

        # if we've reached the end
        if idx >= len(observations):
            # stop the timer
            window._truck_replay_timer.stop()

            # log action
            log.info("truck replay complete")
            return

        # if there is still data to parse through, update the vehicle
        window.update_vehicle_obs(observations[idx])

        # increment the index
        idx += 1

    # create the timer
    window._truck_replay_timer = QTimer(window)
    window._truck_replay_timer.setInterval(interval_ms)
    window._truck_replay_timer.timeout.connect(tick)

    # start the timer
    def start_replay():
        tick()
        window._truck_replay_timer.start()

    # start the replay
    QTimer.singleShot(1400, start_replay)

# if this is the main script
if __name__ == "__main__":
    main()
