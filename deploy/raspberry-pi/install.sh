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

if [ -s "$CONFIG_DIR/runtime.env" ]; then
    . "$CONFIG_DIR/runtime.env"
else
    JITTERSIM_UPLINK=eth0
    JITTERSIM_TEST_INTERFACE=wlan0
fi
python3 "$SOURCE_DIR/deploy/raspberry-pi/preflight.py" \
    --uplink "$JITTERSIM_UPLINK" --test-interface "$JITTERSIM_TEST_INTERFACE"

existing_unbound=no
if dpkg-query -W -f='${Status}' unbound 2>/dev/null | grep -q 'install ok installed'; then
    existing_unbound=yes
fi
existing_hostapd=no
if dpkg-query -W -f='${Status}' hostapd 2>/dev/null | grep -q 'install ok installed'; then
    existing_hostapd=yes
fi
apt-get update
apt-get install -y dnsmasq-base iputils-ping iw nftables openssl python3-venv tcpdump unbound dnsutils hostapd conntrack
# New package defaults must not take over existing DNS or Wi-Fi services.
if [ "$existing_unbound" = no ]; then systemctl disable --now unbound.service; fi
if [ "$existing_hostapd" = no ]; then systemctl disable --now hostapd.service; fi

install -d -m 0750 -o root -g jittersim "$CONFIG_DIR"
install -d -m 0750 -o jittersim -g jittersim "$DATA_DIR"
chown -R jittersim:jittersim "$DATA_DIR"
install -d -m 0755 -o root -g root "$INSTALL_DIR"

if [ "$SOURCE_DIR" != "$INSTALL_DIR" ]; then
    cp -R "$SOURCE_DIR/pyproject.toml" "$SOURCE_DIR/README.md" \
        "$SOURCE_DIR/src" "$SOURCE_DIR/deploy" "$INSTALL_DIR/"
fi

python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install "$INSTALL_DIR"
# jittersim-helper runs as root from this directory, so only root may change it.
chown -R root:root "$INSTALL_DIR"
chmod -R go-w "$INSTALL_DIR"
# `sudo jittersim normal-access` clears restrictions when the dashboard is down.
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
        printf 'JITTERSIM_AP_SSID=TestNet\n'
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

. "$CONFIG_DIR/ap.env"
# Test Wi-Fi clients can't reach each other unless ap.env sets
# JITTERSIM_AP_ISOLATION=no.
ap_isolation="${JITTERSIM_AP_ISOLATION:-yes}"
case "$ap_isolation" in
    yes|no) ;;
    *) echo "JITTERSIM_AP_ISOLATION must be yes or no" >&2; exit 1 ;;
esac
ap_active_connection=$(nmcli -g GENERAL.CONNECTION device show "$JITTERSIM_TEST_INTERFACE")
ap_settings_before=
if ! nmcli connection show jittersim-ap >/dev/null 2>&1; then
    nmcli connection add type wifi ifname "$JITTERSIM_TEST_INTERFACE" \
        con-name jittersim-ap ssid "$JITTERSIM_AP_SSID" autoconnect no
    nmcli connection modify jittersim-ap \
        802-11-wireless-security.key-mgmt wpa-psk \
        802-11-wireless-security.psk "$JITTERSIM_AP_PASSWORD"
else
    ap_settings_before=$(nmcli -g \
        connection.interface-name,connection.autoconnect,802-11-wireless.mode,802-11-wireless.ap-isolation,ipv4.method,ipv4.addresses,ipv4.shared-dhcp-range,ipv6.method \
        connection show jittersim-ap)
fi
if ! nmcli -g ipv4.shared-dhcp-range connection show jittersim-ap >/dev/null 2>&1; then
    echo "NetworkManager 1.50 or newer is required for an isolated DHCP range" >&2
    exit 1
fi
nmcli connection modify jittersim-ap \
    connection.interface-name "$JITTERSIM_TEST_INTERFACE" \
    connection.autoconnect no \
    802-11-wireless.mode ap \
    802-11-wireless.ap-isolation "$ap_isolation" \
    ipv4.method shared \
    ipv4.addresses 192.168.50.1/24 \
    ipv4.shared-dhcp-range 192.168.50.100,192.168.50.199 \
    ipv6.method disabled
ap_settings_after=$(nmcli -g \
    connection.interface-name,connection.autoconnect,802-11-wireless.mode,802-11-wireless.ap-isolation,ipv4.method,ipv4.addresses,ipv4.shared-dhcp-range,ipv6.method \
    connection show jittersim-ap)

dashboard_ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (p = 1; p <= NF; p++) if ($p == "src") {print $(p + 1); exit}}' || true)
if [ -z "$dashboard_ip" ]; then
    dashboard_ip=127.0.0.1
fi
# Browsers show the certificate's name to anyone who opens the dashboard
# address, so it uses the Pi's own name rather than the product's.
dashboard_host="$(hostname -s).local"
dashboard_cert_matches=false
if [ -s "$CONFIG_DIR/dashboard.crt" ] && [ -s "$CONFIG_DIR/dashboard.key" ]; then
    dashboard_cert_text=$(openssl x509 -in "$CONFIG_DIR/dashboard.crt" -noout -text 2>/dev/null || true)
    if printf '%s\n' "$dashboard_cert_text" | grep -Fq "IP Address:$dashboard_ip" \
        && printf '%s\n' "$dashboard_cert_text" | grep -Fq "DNS:$dashboard_host"; then
        dashboard_cert_matches=true
    fi
fi
if [ "$dashboard_cert_matches" = false ]; then
    umask 077
    openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 825 \
        -keyout "$CONFIG_DIR/dashboard.key" \
        -out "$CONFIG_DIR/dashboard.crt" \
        -subj "/CN=$dashboard_host" \
        -addext "subjectAltName=DNS:$dashboard_host,IP:$dashboard_ip" \
        -addext "extendedKeyUsage=serverAuth"
fi
chown root:jittersim "$CONFIG_DIR/dashboard.key"
chmod 0640 "$CONFIG_DIR/dashboard.key"
chown root:root "$CONFIG_DIR/dashboard.crt"
chmod 0644 "$CONFIG_DIR/dashboard.crt"

sed \
    -e "s/@UPLINK_INTERFACE@/$JITTERSIM_UPLINK/g" \
    -e "s/@TEST_INTERFACE@/$JITTERSIM_TEST_INTERFACE/g" \
    "$SOURCE_DIR/deploy/raspberry-pi/jittersim-control-firewall.nft" \
    > "$CONFIG_DIR/jittersim-control-firewall.nft"
chown root:root "$CONFIG_DIR/jittersim-control-firewall.nft"
chmod 0644 "$CONFIG_DIR/jittersim-control-firewall.nft"
install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/jittersim-control-firewall.service" \
    /etc/systemd/system/jittersim-control-firewall.service
install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/jittersim-helper.service" \
    /etc/systemd/system/jittersim-helper.service
install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/jittersim-dashboard.service" \
    /etc/systemd/system/jittersim-dashboard.service
install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/jittersim-browser-proxy.service" \
    /etc/systemd/system/jittersim-browser-proxy.service

install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/jittersim-update.service" \
    /etc/systemd/system/jittersim-update.service

# Recovery must run even if an interrupted package install damages the venv.
install -d -m 0755 -o root -g root /usr/local/lib
install -m 0644 -o root -g root "$SOURCE_DIR/src/jittersim/updates.py" /usr/local/lib/jittersim-update.py
systemctl enable jittersim-update.service

for access_service in jittersim-dns jittersim-access-boot jittersim-ap; do
    install -m 0644 -o root -g root "$SOURCE_DIR/deploy/raspberry-pi/$access_service.service" \
        "/etc/systemd/system/$access_service.service"
done
install -d -m 0750 -o root -g jittersim /var/lib/jittersim-access
systemctl daemon-reload
systemctl enable jittersim-access-boot.service jittersim-ap.service
systemctl restart jittersim-access-boot.service
systemctl enable jittersim-control-firewall.service
systemctl restart jittersim-control-firewall.service
systemctl enable jittersim-helper.service jittersim-dashboard.service jittersim-browser-proxy.service
systemctl restart jittersim-helper.service
systemctl restart jittersim-dashboard.service
if [ "$ap_active_connection" = jittersim-ap ]; then
    if [ "$ap_settings_before" != "$ap_settings_after" ]; then
        echo "AP settings were saved but the active Wi-Fi was not restarted." >&2
        echo "To use them now, run: sudo nmcli connection up jittersim-ap" >&2
        echo "Test Wi-Fi devices reconnect after a few seconds. A reboot also works." >&2
    fi
else
    systemctl start jittersim-ap.service
fi
systemctl restart jittersim-browser-proxy.service

echo "JitterSim installed. Read the generated credentials locally from:" >&2
echo "  $CONFIG_DIR/control.env" >&2
echo "  $CONFIG_DIR/ap.env" >&2
echo "Dashboard certificate fingerprint (verify before trusting):" >&2
openssl x509 -in "$CONFIG_DIR/dashboard.crt" -noout -fingerprint -sha256 >&2
