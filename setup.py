#!/usr/bin/env python3
"""
setup.py — cross-platform one-time setup for STORM.

Prerequisites: Python 3.8+
  If you don't have conda, install Miniforge first:
    https://github.com/conda-forge/miniforge#download

Usage:
    python setup.py
"""

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_YML = os.path.join(PROJECT_DIR, "envs", "storm.yml")
SYSTEM = platform.system()  # "Darwin", "Linux", "Windows"



def _conda_search_dirs():
    """Return platform-appropriate directories to search for conda."""
    home = os.path.expanduser("~")
    dirs = [
        os.path.join(home, "miniforge3"),
        os.path.join(home, "miniconda3"),
        os.path.join(home, "anaconda3"),
    ]
    if SYSTEM == "Darwin":
        dirs += [
            os.path.join(home, "opt", "miniforge3"),
            os.path.join(home, "opt", "miniconda3"),
            os.path.join(home, "opt", "anaconda3"),
            "/opt/homebrew/Caskroom/miniforge/base",
            "/opt/miniconda3",
            "/opt/anaconda3",
        ]
    elif SYSTEM == "Linux":
        dirs += [
            os.path.join(home, "opt", "miniforge3"),
            "/opt/miniforge3",
            "/opt/miniconda3",
            "/opt/anaconda3",
        ]
    elif SYSTEM == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        pdata = os.environ.get("PROGRAMDATA", "")
        dirs += [
            os.path.join(local, "miniforge3"),
            os.path.join(local, "miniconda3"),
            os.path.join(local, "anaconda3"),
            os.path.join(pdata, "miniforge3"),
            os.path.join(pdata, "miniconda3"),
            os.path.join(pdata, "anaconda3"),
            r"C:\miniforge3",
            r"C:\miniconda3",
            r"C:\anaconda3",
        ]
    return dirs


def find_conda():
    """Return the path to the conda executable, or None."""
    conda = shutil.which("conda")
    if conda:
        return conda

    for d in _conda_search_dirs():
        if SYSTEM == "Windows":
            candidate = os.path.join(d, "condabin", "conda.bat")
        else:
            candidate = os.path.join(d, "condabin", "conda")
        if os.path.isfile(candidate):
            return candidate
    return None


def install_miniforge():
    """Download and silently install Miniforge, return conda path."""
    home = os.path.expanduser("~")
    install_dir = os.path.join(home, "miniforge3")
    base_url = "https://github.com/conda-forge/miniforge/releases/latest/download"

    if SYSTEM == "Darwin":
        arch = "arm64" if platform.machine() == "arm64" else "x86_64"
        filename = f"Miniforge3-MacOSX-{arch}.sh"
    elif SYSTEM == "Linux":
        filename = f"Miniforge3-Linux-{platform.machine()}.sh"
    elif SYSTEM == "Windows":
        filename = "Miniforge3-Windows-x86_64.exe"
    else:
        sys.exit(f"Unsupported platform: {SYSTEM}")

    url = f"{base_url}/{filename}"
    installer = os.path.join(tempfile.gettempdir(), filename)

    print(f"Downloading Miniforge from {url} ...")
    urllib.request.urlretrieve(url, installer)

    print(f"Installing Miniforge to {install_dir} ...")
    if SYSTEM == "Windows":
        subprocess.check_call([
            installer, "/S", "/InstallationType=JustMe",
            "/RegisterPython=0", f"/D={install_dir}",
        ])
        conda = os.path.join(install_dir, "condabin", "conda.bat")
    else:
        subprocess.check_call(["bash", installer, "-b", "-p", install_dir])
        conda = os.path.join(install_dir, "condabin", "conda")

    os.remove(installer)
    print("Miniforge installed.")
    return conda



def env_exists(conda):
    """Check if the 'storm' conda environment already exists."""
    result = subprocess.run(
        [conda, "env", "list"],
        capture_output=True, text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("storm "):
            return True
    return False


def create_env(conda):
    """Create the storm conda environment from the unified yml."""
    if env_exists(conda):
        # an environment made from an older envs/storm.yml lacks packages
        # added since (e.g. arm_pyart, for dealiasing): add them. No
        # --prune, so nothing someone installed by hand is removed.
        print("storm environment already exists — updating it from envs/storm.yml...")
        subprocess.check_call([conda, "env", "update", "-n", "storm", "-f", ENV_YML])
        print("storm environment updated.")
        return
    print("Creating storm conda environment (this may take a few minutes)...")
    subprocess.check_call([conda, "env", "create", "-f", ENV_YML])
    print("storm environment created.")



def install_linux_deps():
    """Install Qt6 WebEngine system dependencies on Linux."""
    print("Installing Qt6 WebEngine system dependencies...")
    if shutil.which("apt-get"):
        subprocess.check_call([
            "sudo", "apt-get", "install", "-y",
            "libnss3", "libxss1", "libasound2", "libatk-bridge2.0-0",
            "libgtk-3-0", "libx11-xcb1", "libxcomposite1", "libxdamage1",
            "libxrandr2", "desktop-file-utils",
        ])
    elif shutil.which("dnf"):
        subprocess.check_call([
            "sudo", "dnf", "install", "-y",
            "nss", "libXScrnSaver", "alsa-lib", "at-spi2-atk", "gtk3",
            "libX11", "libXcomposite", "libXdamage", "libXrandr",
            "libxkbcommon", "libxkbcommon-x11",
            "xcb-util", "xcb-util-cursor", "xcb-util-image",
            "xcb-util-keysyms", "xcb-util-renderutil", "xcb-util-wm", "libxcb",
            "desktop-file-utils",
        ])
    elif shutil.which("yum"):
        subprocess.check_call([
            "sudo", "yum", "install", "-y",
            "nss", "libXScrnSaver", "alsa-lib", "at-spi2-atk", "gtk3",
            "libX11", "libXcomposite", "libXdamage", "libXrandr",
            "libxkbcommon", "libxkbcommon-x11",
            "xcb-util", "xcb-util-cursor", "xcb-util-image",
            "xcb-util-keysyms", "xcb-util-renderutil", "xcb-util-wm", "libxcb",
            "desktop-file-utils",
        ])
    else:
        print("WARNING: Could not detect package manager (apt/dnf/yum).")
        print("You may need to manually install Qt6 WebEngine system libraries.")



def create_shortcuts_mac():
    """Build STORM.app and place a Desktop symlink."""
    create_app = os.path.join(PROJECT_DIR, "scripts", "create_app.sh")
    print("Building STORM.app...")
    subprocess.check_call(["bash", create_app])
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        link = os.path.join(desktop, "STORM.app")
        if os.path.islink(link) or os.path.exists(link):
            os.remove(link)
        os.symlink(os.path.join(PROJECT_DIR, "STORM.app"), link)
        print("STORM shortcut placed on Desktop.")


def create_shortcuts_linux(conda):
    """Create a launcher script and .desktop entry."""
    # launcher script
    launcher = os.path.join(PROJECT_DIR, "storm_launcher.sh")
    conda_base = subprocess.check_output(
        [conda, "info", "--base"], text=True,
    ).strip()
    with open(launcher, "w") as f:
        f.write(f'#!/bin/bash\n'
                f'source "{conda_base}/etc/profile.d/conda.sh"\n'
                f'conda activate storm\n'
                f'cd "{PROJECT_DIR}"\n'
                f'python main.py "$@"\n')
    os.chmod(launcher, 0o755)
    print(f"Launcher written to {launcher}")

    # .desktop entry
    desktop_dir = os.path.join(os.path.expanduser("~"), ".local", "share", "applications")
    os.makedirs(desktop_dir, exist_ok=True)
    desktop_file = os.path.join(desktop_dir, "storm.desktop")
    icon_path = os.path.join(PROJECT_DIR, "storm.png")
    icon = icon_path if os.path.isfile(icon_path) else "storm"
    with open(desktop_file, "w") as f:
        f.write(f"[Desktop Entry]\n"
                f"Name=STORM\n"
                f"Comment=Severe-weather Tactical Operations and Response Manager\n"
                f"Exec={launcher}\n"
                f"Icon={icon}\n"
                f"Terminal=false\n"
                f"Type=Application\n"
                f"Categories=Science;\n")
    os.chmod(desktop_file, 0o755)

    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", desktop_dir], check=False)

    print(f".desktop entry written to {desktop_file}")

    # desktop shortcut
    user_desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(user_desktop):
        dest = os.path.join(user_desktop, "storm.desktop")
        shutil.copy2(desktop_file, dest)
        os.chmod(dest, 0o755)
        if shutil.which("gio"):
            subprocess.run(
                ["gio", "set", dest, "metadata::trusted", "true"],
                check=False, capture_output=True,
            )
        print("STORM shortcut placed on Desktop.")


def create_shortcuts_windows():
    """Create Desktop and Start Menu shortcuts via PowerShell."""
    create_app = os.path.join(PROJECT_DIR, "scripts", "create_app_windows.bat")
    if os.path.isfile(create_app):
        print("Creating STORM shortcuts...")
        subprocess.check_call([create_app], shell=True)



def main():
    print(f"STORM setup — {SYSTEM} ({platform.machine()})")
    print()

    # 0. linux system deps
    if SYSTEM == "Linux":
        install_linux_deps()

    # 1. find or install conda
    conda = find_conda()
    if not conda:
        print("Conda not found.")
        reply = input("Install Miniforge automatically? [Y/n] ").strip().lower()
        if reply in ("", "y", "yes"):
            conda = install_miniforge()
        else:
            print("Please install Miniforge and re-run setup:")
            print("  https://github.com/conda-forge/miniforge#download")
            sys.exit(1)

    print(f"Using conda: {conda}")
    print()

    # 2. create environment
    create_env(conda)
    print()

    # 3. shortcuts
    if SYSTEM == "Darwin":
        create_shortcuts_mac()
    elif SYSTEM == "Linux":
        create_shortcuts_linux(conda)
    elif SYSTEM == "Windows":
        create_shortcuts_windows()

    print()
    print("━" * 48)
    print("  Setup complete!")
    print("  Launch STORM from your shortcut or run:")
    print("    conda activate storm && python main.py")
    print("━" * 48)


if __name__ == "__main__":
    main()
