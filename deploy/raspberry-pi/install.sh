#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "run this installer as root" >&2
    exit 1
fi

SOURCE_DIR=${1:-$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)}
INSTALL_DIR=/opt/jittersim
CONFIG_DIR=/etc/jittersim
DATA_DIR=/var/lib/jittersim

if [ ! -f "$SOURCE_DIR/pyproject.toml" ]; then
    echo "source directory does not contain pyproject.toml: $SOURCE_DIR" >&2
    exit 1
fi

if ! getent passwd jittersim >/dev/null; then
    echo "the jittersim service user must be created by Raspberry Pi Imager" >&2
    exit 1
fi

apt-get update
apt-get install -y nftables openssl python3-venv tcpdump

install -d -m 0750 -o root -g jittersim "$CONFIG_DIR"
install -d -m 0750 -o jittersim -g jittersim "$DATA_DIR"
chown -R jittersim:jittersim "$DATA_DIR"
install -d -m 0755 -o root -g root "$INSTALL_DIR"

if [ "$SOURCE_DIR" != "$INSTALL_DIR" ]; then
    cp -a "$SOURCE_DIR"/. "$INSTALL_DIR"/
fi

python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install "$INSTALL_DIR"
# jittersim-helper runs as root from this directory, so only root may change it.
chown -R root:root "$INSTALL_DIR"
chmod -R go-w "$INSTALL_DIR"
# `sudo jittersim reset` clears the network over SSH when the dashboard is down.
ln -sf "$INSTALL_DIR/.venv/bin/jittersim" /usr/local/bin/jittersim

if [ ! -s "$CONFIG_DIR/control.env" ]; then
    admin_token=$(openssl rand -hex 32)
    umask 077
    printf 'JITTERSIM_ADMIN_TOKEN=%s\n' "$admin_token" > "$CONFIG_DIR/control.env"
fi
chown root:root "$CONFIG_DIR/control.env"
chmod 0600 "$CONFIG_DIR/control.env"

if [ ! -s "$CONFIG_DIR/ap.env" ]; then
    ap_password=$(openssl rand -hex 12)
    umask 077
    {
        printf 'JITTERSIM_AP_SSID=JitterSim-Test\n'
        printf 'JITTERSIM_AP_PASSWORD=%s\n' "$ap_password"
    } > "$CONFIG_DIR/ap.env"
fi
chown root:root "$CONFIG_DIR/ap.env"
chmod 0600 "$CONFIG_DIR/ap.env"

if [ ! -s "$CONFIG_DIR/runtime.env" ]; then
    umask 077
    {
        printf 'JITTERSIM_UPLINK=eth0\n'
        printf 'JITTERSIM_TEST_INTERFACE=wlan0\n'
    } > "$CONFIG_DIR/runtime.env"
fi
chown root:root "$CONFIG_DIR/runtime.env"
chmod 0600 "$CONFIG_DIR/runtime.env"

dashboard_ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (p = 1; p <= NF; p++) if ($p == "src") {print $(p + 1); exit}}' || true)
if [ -z "$dashboard_ip" ]; then
    dashboard_ip=127.0.0.1
fi
dashboard_cert_matches_ip=false
if [ -s "$CONFIG_DIR/dashboard.crt" ] && [ -s "$CONFIG_DIR/dashboard.key" ] \
    && openssl x509 -in "$CONFIG_DIR/dashboard.crt" -noout -text 2>/dev/null \
        | grep -Fq "IP Address:$dashboard_ip"; then
    dashboard_cert_matches_ip=true
fi
if [ "$dashboard_cert_matches_ip" = false ]; then
    umask 077
    openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 825 \
        -keyout "$CONFIG_DIR/dashboard.key" \
        -out "$CONFIG_DIR/dashboard.crt" \
        -subj "/CN=jittersim.local" \
        -addext "subjectAltName=DNS:jittersim.local,IP:$dashboard_ip" \
        -addext "extendedKeyUsage=serverAuth"
fi
chown root:jittersim "$CONFIG_DIR/dashboard.key"
chmod 0640 "$CONFIG_DIR/dashboard.key"
chown root:root "$CONFIG_DIR/dashboard.crt"
chmod 0644 "$CONFIG_DIR/dashboard.crt"

install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/jittersim-control-firewall.nft" \
    "$CONFIG_DIR/jittersim-control-firewall.nft"
install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/jittersim-control-firewall.service" \
    /etc/systemd/system/jittersim-control-firewall.service
install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/jittersim-helper.service" \
    /etc/systemd/system/jittersim-helper.service
install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/jittersim-dashboard.service" \
    /etc/systemd/system/jittersim-dashboard.service

systemctl daemon-reload
systemctl enable jittersim-control-firewall.service
systemctl restart jittersim-control-firewall.service
systemctl enable jittersim-helper.service jittersim-dashboard.service
systemctl restart jittersim-helper.service
systemctl restart jittersim-dashboard.service

echo "JitterSim installed. Read the generated credentials locally from:" >&2
echo "  $CONFIG_DIR/control.env" >&2
echo "  $CONFIG_DIR/ap.env" >&2
echo "Dashboard certificate fingerprint (verify before trusting):" >&2
openssl x509 -in "$CONFIG_DIR/dashboard.crt" -noout -fingerprint -sha256 >&2
