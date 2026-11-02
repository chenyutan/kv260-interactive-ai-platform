#!/bin/sh
set -eu
if [ "${BODYINK_VIEWER:-native}" = native ]; then
    export BODYINK_RAW_PREVIEW="${BODYINK_RAW_PREVIEW:-1}"
    export BODYINK_PREVIEW_STREAM="${BODYINK_PREVIEW_STREAM:-1}"
    exec /usr/bin/python3 "$(dirname "$0")/native_display.py"
fi
profile="$HOME/snap/firefox/common/bodyink-display"
mkdir -p "$profile"
cat > "$profile/user.js" <<'PREFS'
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.tabs.warnOnClose", false);
PREFS
for attempt in $(seq 1 60); do
    if curl -fsS --max-time 2 -o /dev/null http://127.0.0.1:8088/; then break; fi
    sleep 1
done
# Keep standard window controls; users may toggle fullscreen with F11.
exec /usr/bin/firefox --no-remote --profile "$profile" --new-window http://127.0.0.1:8088/
