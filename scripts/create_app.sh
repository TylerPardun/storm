#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # project root
APP_NAME="STORM"
APP_BUNDLE="$PROJECT_DIR/$APP_NAME.app"
MACOS_DIR="$APP_BUNDLE/Contents/MacOS"
RESOURCES_DIR="$APP_BUNDLE/Contents/Resources"

echo "Building $APP_BUNDLE ..."

rm -rf "$APP_BUNDLE"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

# placed alongside the main executable as storm_launcher.sh.

cat > "$MACOS_DIR/storm_launcher.sh" << 'LAUNCHER'
#!/bin/bash

# project root baked in at build time — app can be moved or symlinked freely.
PROJECT_DIR="__PROJECT_DIR__"

CONDA_BASE=""
for DIR in \
    "$HOME/miniforge3" \
    "$HOME/opt/miniforge3" \
    "$HOME/miniforge" \
    "$HOME/miniconda3" \
    "$HOME/opt/miniconda3" \
    "$HOME/anaconda3" \
    "$HOME/opt/anaconda3" \
    "/opt/homebrew/Caskroom/miniforge/base" \
    "/opt/miniconda3" \
    "/opt/anaconda3"; do
    if [ -d "$DIR" ]; then
        CONDA_BASE="$DIR"
        break
    fi
done

if [ -z "$CONDA_BASE" ]; then
    osascript -e 'display alert "STORM — Launch Error" message "Could not find a conda installation.\n\nPlease install miniforge or miniconda and create the \"storm\" environment." as critical'
    exit 1
fi

# bypass "conda activate" — it requires an interactive shell and silently fails
PYTHON=""
ENV_PREFIX=""
for ENV_NAME in storm storm311; do
    CANDIDATE="$CONDA_BASE/envs/$ENV_NAME/bin/python"
    if [ -f "$CANDIDATE" ]; then
        PYTHON="$CANDIDATE"
        ENV_PREFIX="$CONDA_BASE/envs/$ENV_NAME"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    osascript -e 'display alert "STORM — Launch Error" message "The \"storm\" conda environment was not found.\n\nCreate it with:\n  python setup.py" as critical'
    exit 1
fi

# set PATH so any subprocesses (Flask, etc.) find the right binaries
export PATH="$ENV_PREFIX/bin:$CONDA_BASE/bin:$CONDA_BASE/condabin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export CONDA_PREFIX="$ENV_PREFIX"

# use certifi's CA bundle — macOS app bundles don't inherit the system SSL certs
CERTIFI_CERTS="$(ls "$ENV_PREFIX"/lib/python3.*/site-packages/certifi/cacert.pem 2>/dev/null | head -1)"
if [ -f "$CERTIFI_CERTS" ]; then
    export SSL_CERT_FILE="$CERTIFI_CERTS"
    export REQUESTS_CA_BUNDLE="$CERTIFI_CERTS"
fi

cd "$PROJECT_DIR"
exec "$PYTHON" main.py
LAUNCHER

# bake in the project directory path
sed -i '' "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$MACOS_DIR/storm_launcher.sh"

chmod +x "$MACOS_DIR/storm_launcher.sh"

# macOS Finder (Sequoia+) silently refuses to launch shell-script app bundles.

cat > /tmp/storm_wrapper.c << 'CWRAPPER'
#include <mach-o/dyld.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>

int main(void) {
    char self[4096];
    uint32_t n = sizeof(self);
    _NSGetExecutablePath(self, &n);

    /* strip the executable filename, append storm_launcher.sh */
    char *slash = strrchr(self, '/');
    if (slash) slash[1] = '\0';
    strncat(self, "storm_launcher.sh", sizeof(self) - strlen(self) - 1);

    execl("/bin/bash", "/bin/bash", self, NULL);
    return 1;
}
CWRAPPER

cc -o "$MACOS_DIR/$APP_NAME" /tmp/storm_wrapper.c
rm -f /tmp/storm_wrapper.c

cat > "$APP_BUNDLE/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>STORM</string>
    <key>CFBundleDisplayName</key>
    <string>STORM</string>
    <key>CFBundleIdentifier</key>
    <string>edu.ou.nssl.storm</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleExecutable</key>
    <string>STORM</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleIconFile</key>
    <string>storm</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSUIElement</key>
    <false/>
</dict>
</plist>
PLIST

if [ -f "$PROJECT_DIR/storm.icns" ]; then
    cp "$PROJECT_DIR/storm.icns" "$RESOURCES_DIR/storm.icns"
    echo "  Icon: storm.icns copied."
else
    echo "  Icon: no storm.icns found — app will use a default icon."
    echo "        To add one: save a 1024×1024 PNG as storm.png and run:"
    echo "          python scripts/create_icon.py"
fi

echo ""
echo "Done.  STORM.app created at:"
echo "  $APP_BUNDLE"
echo ""
echo "You can drag it to your Dock or Applications folder."
