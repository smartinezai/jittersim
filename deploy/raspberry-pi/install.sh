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
python3 "$SOURCE_DIR/deploy/raspberry-pi/retire-services.py" --check

apt-get install -y dnsmasq-base iw wpasupplicant nftables openssl python3-venv tcpdump

# Verify the actual kernel hook before replacing live profile rules.
python3 "$SOURCE_DIR/deploy/raspberry-pi/check-egress-hook.py" "$JITTERSIM_TEST_INTERFACE"

install -d -m 0750 -o root -g jittersim "$CONFIG_DIR"
install -d -m 0750 -o jittersim -g jittersim "$DATA_DIR"
chown -R jittersim:jittersim "$DATA_DIR"
install -d -m 0755 -o root -g root "$INSTALL_DIR"

python3 "$SOURCE_DIR/deploy/raspberry-pi/replace-source.py" "$SOURCE_DIR" "$INSTALL_DIR"

python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install "$INSTALL_DIR"
chown -R root:root "$INSTALL_DIR"
chmod -R go-w "$INSTALL_DIR"
"$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/deploy/raspberry-pi/bootstrap-dashboard-access.py"
ln -sfn "$INSTALL_DIR/.venv/bin/jittersim" /usr/local/bin/jittersim
python3 "$INSTALL_DIR/deploy/raspberry-pi/retire-services.py"

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
        printf 'JITTERSIM_AP_SSID=JitterSim\n'
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
ap_active_connection=$(nmcli -g GENERAL.CONNECTION device show "$JITTERSIM_TEST_INTERFACE")
ap_settings_before=
if ! nmcli connection show jittersim-ap >/dev/null 2>&1; then
    nmcli connection add type wifi ifname "$JITTERSIM_TEST_INTERFACE" \
        con-name jittersim-ap ssid "$JITTERSIM_AP_SSID" autoconnect no
    nmcli connection modify jittersim-ap \
        802-11-wireless-security.key-mgmt sae \
        802-11-wireless-security.proto rsn \
        802-11-wireless-security.pairwise ccmp \
        802-11-wireless-security.group ccmp \
        802-11-wireless-security.pmf 3 \
        802-11-wireless-security.psk "$JITTERSIM_AP_PASSWORD"
else
    ap_settings_before=$(nmcli -g \
        connection.interface-name,connection.autoconnect,802-11-wireless.mode,ipv4.method,ipv4.addresses,ipv4.shared-dhcp-range,ipv6.method,802-11-wireless-security.key-mgmt,802-11-wireless-security.proto,802-11-wireless-security.pairwise,802-11-wireless-security.group,802-11-wireless-security.pmf \
        connection show jittersim-ap)
fi
if ! nmcli -g ipv4.shared-dhcp-range connection show jittersim-ap >/dev/null 2>&1; then
    echo "NetworkManager 1.50 or newer is required for an isolated DHCP range" >&2
    exit 1
fi
nmcli connection modify jittersim-ap \
    connection.interface-name "$JITTERSIM_TEST_INTERFACE" \
    connection.autoconnect yes \
    802-11-wireless.mode ap \
    ipv4.method shared \
    ipv4.addresses 192.168.50.1/24 \
    ipv4.shared-dhcp-range 192.168.50.100,192.168.50.199 \
    ipv6.method disabled
ap_settings_after=$(nmcli -g \
    connection.interface-name,connection.autoconnect,802-11-wireless.mode,ipv4.method,ipv4.addresses,ipv4.shared-dhcp-range,ipv6.method,802-11-wireless-security.key-mgmt,802-11-wireless-security.proto,802-11-wireless-security.pairwise,802-11-wireless-security.group,802-11-wireless-security.pmf \
    connection show jittersim-ap)

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

systemctl daemon-reload
systemctl enable jittersim-control-firewall.service
systemctl restart jittersim-control-firewall.service
systemctl enable jittersim-helper.service jittersim-dashboard.service jittersim-browser-proxy.service
systemctl restart jittersim-helper.service
systemctl restart jittersim-dashboard.service
if [ "$ap_active_connection" = jittersim-ap ]; then
    if [ "$ap_settings_before" != "$ap_settings_after" ]; then
        echo "AP settings were saved but the active Wi-Fi was not restarted." >&2
        echo "Restart jittersim-ap during a planned maintenance window." >&2
    fi
else
    nmcli connection up jittersim-ap
fi
systemctl restart jittersim-browser-proxy.service
# Remove obsolete DNS access rules even when an older database has no saved action.
"$INSTALL_DIR/.venv/bin/python" -m jittersim.networking.legacy

setup_dashboard_name_service() {
    apt-get install -y avahi-daemon avahi-utils || return 1
    install -d -m 0755 -o root -g root /etc/NetworkManager/dispatcher.d || return 1
    install -m 0644 -o root -g root \
        "$SOURCE_DIR/deploy/raspberry-pi/jittersim-mdns.service" \
        /etc/systemd/system/jittersim-mdns.service || return 1
    install -m 0755 -o root -g root \
        "$SOURCE_DIR/deploy/raspberry-pi/publish-dashboard-name.sh" \
        "$INSTALL_DIR/deploy/raspberry-pi/publish-dashboard-name.sh" || return 1
    install -m 0755 -o root -g root \
        "$SOURCE_DIR/deploy/raspberry-pi/jittersim-mdns-dispatcher" \
        /etc/NetworkManager/dispatcher.d/80-jittersim-mdns || return 1
    systemctl daemon-reload || return 1
    systemctl enable jittersim-mdns.service || return 1
    systemctl restart jittersim-mdns.service || return 1
}

if setup_dashboard_name_service; then
    if systemctl is-active --quiet jittersim-mdns.service; then
        echo "Dashboard name publisher is active. Confirm jittersim.local from the management network." >&2
    else
        echo "Warning: dashboard name publication is not active. Use the Pi's Ethernet address." >&2
    fi
else
    echo "Warning: local dashboard name setup failed. Use the Pi's Ethernet address." >&2
fi

echo "JitterSim installed. Read the generated credentials locally from:" >&2
echo "  $CONFIG_DIR/control.env" >&2
echo "  $CONFIG_DIR/ap.env" >&2
echo "Dashboard certificate fingerprint (verify before trusting):" >&2
openssl x509 -in "$CONFIG_DIR/dashboard.crt" -noout -fingerprint -sha256 >&2
