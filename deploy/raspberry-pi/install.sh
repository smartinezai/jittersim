#!/bin/sh
set -eu
# Never trace credentials inherited from the caller or read below.
set +x
unset JITTERSIM_AP_PASSWORD ap_password jittersim_initial_ap_secret

if [ "$(id -u)" -ne 0 ]; then
    echo "run this installer as root" >&2
    exit 1
fi

# Program files must remain readable by the service user. Credentials below
# use explicit private permissions.
umask 022

# Passwordless staging stays off unless the owner asks for it here or later with
# 'sudo jittersim update enable-passwordless-staging'. An earlier choice is kept.
enable_passwordless_staging=no
if [ "${1:-}" = --enable-passwordless-staging ]; then
    enable_passwordless_staging=yes
    shift
fi

SOURCE_DIR=${1:-$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)}
INSTALL_DIR=/opt/jittersim
CONFIG_DIR=/etc/jittersim
DATA_DIR=/var/lib/jittersim

if [ ! -f "$SOURCE_DIR/pyproject.toml" ]; then
    echo "source directory does not contain pyproject.toml: $SOURCE_DIR" >&2
    exit 1
fi

if [ "${JITTERSIM_INSTALL_LOCK_FD:-}" != 9 ]; then
    if [ "$enable_passwordless_staging" = yes ]; then
        exec python3 -I "$SOURCE_DIR/deploy/raspberry-pi/install-lock.py" \
            --installer "$0" --source "$SOURCE_DIR" --enable-passwordless-staging
    fi
    exec python3 -I "$SOURCE_DIR/deploy/raspberry-pi/install-lock.py" \
        --installer "$0" --source "$SOURCE_DIR"
fi
python3 -I "$SOURCE_DIR/deploy/raspberry-pi/install-lock.py" --verify
python3 -I "$SOURCE_DIR/deploy/raspberry-pi/create-ap-profile.py" --help >/dev/null

# Refuse source that cannot preserve recovery before changing packages or files.
python3 -I "$SOURCE_DIR/src/jittersim/updates.py" check-manager "$SOURCE_DIR/src/jittersim/updates.py"
python3 -I "$SOURCE_DIR/src/jittersim/updates.py" check-operation
python3 -I "$SOURCE_DIR/src/jittersim/updates.py" prepare-installer "$SOURCE_DIR"

# Fixed packages are prepared before live source replacement.
locked_release_wheels=
if [ -f "$SOURCE_DIR/deploy/raspberry-pi/prepare-locked-wheels.py" ]; then
    python3 -I "$SOURCE_DIR/deploy/raspberry-pi/prepare-locked-wheels.py" --source "$SOURCE_DIR" --check
fi

# The passwordless rule is only safe with a trusted release signing key: check it before
# anything changes.
if [ "$enable_passwordless_staging" = yes ] \
    && ! grep -q '^jittersim-release ' "$CONFIG_DIR/release-signers" 2>/dev/null; then
    echo "--enable-passwordless-staging needs a trusted release signing key first." >&2
    echo "Run 'sudo jittersim update trust-key PUBLIC_KEY_FILE' and try again." >&2
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

# An upgrade clears the saved DNS names once when protected devices exist. Say so before it happens.
python3 "$SOURCE_DIR/deploy/raspberry-pi/upgrade-notice.py" "$DATA_DIR/jittersim.db" || true

existing_unbound=no
if dpkg-query -W -f='${Status}' unbound 2>/dev/null | grep -q 'install ok installed'; then
    existing_unbound=yes
fi
existing_hostapd=no
if dpkg-query -W -f='${Status}' hostapd 2>/dev/null | grep -q 'install ok installed'; then
    existing_hostapd=yes
fi
apt-get update
apt-get install -y dnsmasq-base iputils-ping iw wpasupplicant nftables openssl python3-venv tcpdump unbound dnsutils hostapd conntrack
# New package defaults must not take over existing DNS or Wi-Fi services.
if [ "$existing_unbound" = no ]; then systemctl disable --now unbound.service; fi
if [ "$existing_hostapd" = no ]; then systemctl disable --now hostapd.service; fi

# Verify the actual kernel hook before replacing live profile rules.
python3 "$SOURCE_DIR/deploy/raspberry-pi/check-egress-hook.py" "$JITTERSIM_TEST_INTERFACE"

install -d -m 0750 -o root -g jittersim "$CONFIG_DIR"
install -d -m 0750 -o jittersim -g jittersim "$DATA_DIR"
chown -R jittersim:jittersim "$DATA_DIR"
install -d -m 0755 -o root -g root "$INSTALL_DIR"

# Releases with household access rules install jittersim-access-restore. Without it
# the running release (the Pi line before the main merge) never wrote access state.
previous_access_release=no
if [ -x "$INSTALL_DIR/.venv/bin/jittersim-access-restore" ]; then
    previous_access_release=yes
fi

if [ -f "$SOURCE_DIR/deploy/raspberry-pi/prepare-locked-wheels.py" ]; then
    locked_release_wheels=$(mktemp -d /var/tmp/jittersim-release-wheels.XXXXXX)
    chmod 0700 "$locked_release_wheels"
    trap 'rm -rf "$locked_release_wheels"' EXIT
    python3 -I "$SOURCE_DIR/deploy/raspberry-pi/prepare-locked-wheels.py" \
        --source "$SOURCE_DIR" --output "$locked_release_wheels"
fi

python3 "$SOURCE_DIR/deploy/raspberry-pi/replace-source.py" "$SOURCE_DIR" "$INSTALL_DIR"

python3 -m venv "$INSTALL_DIR/.venv"
if [ -n "$locked_release_wheels" ]; then
    "$INSTALL_DIR/.venv/bin/python" -I -m pip install --no-index --no-deps --force-reinstall "$locked_release_wheels"/*.whl
    "$INSTALL_DIR/.venv/bin/python" -I -m pip check
else
    "$INSTALL_DIR/.venv/bin/pip" install "$INSTALL_DIR"
fi
# jittersim-helper runs as root from this directory, so only root may change it.
chown -R root:root "$INSTALL_DIR"
chmod -R go-w "$INSTALL_DIR"
runuser -u jittersim -- "$INSTALL_DIR/.venv/bin/python" -I -c \
    "import jittersim.api.server, jittersim.networking.helper, jittersim.networking.browser_proxy, jittersim.networking.dns_watch, jittersim.networking.access, jittersim.dashboard_access_cli; import importlib.util; importlib.import_module('jittersim.networking.adblocking') if importlib.util.find_spec('jittersim.networking.adblocking') else None"
"$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/deploy/raspberry-pi/bootstrap-dashboard-access.py"
# `sudo jittersim normal-access` and `sudo jittersim wifi reset` work when the dashboard is down.
ln -sf "$INSTALL_DIR/.venv/bin/jittersim" /usr/local/bin/jittersim
python3 "$INSTALL_DIR/deploy/raspberry-pi/retire-services.py"

if [ ! -s "$CONFIG_DIR/control.env" ]; then
    admin_token=$(openssl rand -hex 32)
    umask 077
    printf 'JITTERSIM_ADMIN_TOKEN=%s\n' "$admin_token" > "$CONFIG_DIR/control.env"
fi
chown root:root "$CONFIG_DIR/control.env"
chmod 0600 "$CONFIG_DIR/control.env"

if [ -e "$CONFIG_DIR/ap.env" ] || [ -L "$CONFIG_DIR/ap.env" ]; then
    if [ ! -f "$CONFIG_DIR/ap.env" ] || [ -L "$CONFIG_DIR/ap.env" ] || [ ! -s "$CONFIG_DIR/ap.env" ] || [ ! -r "$CONFIG_DIR/ap.env" ]; then
        echo "Saved Wi-Fi credentials need recovery through management access." >&2
        exit 1
    fi
else
    unset ap_password
    ap_password=$(openssl rand -hex 12)
    umask 077
    {
        printf 'JITTERSIM_AP_SSID=TestNet\n'
        printf 'JITTERSIM_AP_PASSWORD=%s\n' "$ap_password"
    } > "$CONFIG_DIR/ap.env"
    unset ap_password
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
unset jittersim_initial_ap_secret
jittersim_initial_ap_secret=$JITTERSIM_AP_PASSWORD
unset JITTERSIM_AP_PASSWORD
# Test Wi-Fi clients can't reach each other unless ap.env sets
# JITTERSIM_AP_ISOLATION=no.
ap_isolation="${JITTERSIM_AP_ISOLATION:-yes}"
case "$ap_isolation" in
    yes|no) ;;
    *) echo "JITTERSIM_AP_ISOLATION must be yes or no" >&2; exit 1 ;;
esac
ap_active_connection=$(nmcli -g GENERAL.CONNECTION device show "$JITTERSIM_TEST_INTERFACE" 9>&-)
ap_settings_before=
ap_profile_state=$(python3 -I "$SOURCE_DIR/deploy/raspberry-pi/create-ap-profile.py" \
    probe --interface "$JITTERSIM_TEST_INTERFACE")
if [ "$ap_profile_state" = absent ]; then
    printf '%s\n' "$jittersim_initial_ap_secret" | \
        python3 -I "$SOURCE_DIR/deploy/raspberry-pi/create-ap-profile.py" \
        create --interface "$JITTERSIM_TEST_INTERFACE" --ssid "$JITTERSIM_AP_SSID"
elif [ "$ap_profile_state" = present ]; then
    ap_settings_before=$(nmcli -g \
        connection.interface-name,connection.autoconnect,802-11-wireless.mode,802-11-wireless.ap-isolation,ipv4.method,ipv4.addresses,ipv4.shared-dhcp-range,ipv6.method,802-11-wireless-security.key-mgmt,802-11-wireless-security.proto,802-11-wireless-security.pairwise,802-11-wireless-security.group,802-11-wireless-security.pmf \
        connection show jittersim-ap 9>&-)
else
    echo "The initial Wi-Fi profile could not be confirmed." >&2
    exit 1
fi
unset jittersim_initial_ap_secret
if ! nmcli -g ipv4.shared-dhcp-range connection show jittersim-ap 9>&- >/dev/null 2>&1; then
    echo "NetworkManager 1.50 or newer is required for an isolated DHCP range" >&2
    exit 1
fi
# Security settings are set when the connection is created and kept on upgrades.
nmcli connection modify jittersim-ap \
    connection.interface-name "$JITTERSIM_TEST_INTERFACE" \
    connection.autoconnect no \
    802-11-wireless.mode ap \
    802-11-wireless.ap-isolation "$ap_isolation" \
    ipv4.method shared \
    ipv4.addresses 192.168.50.1/24 \
    ipv4.shared-dhcp-range 192.168.50.100,192.168.50.199 \
    ipv6.method disabled 9>&-
ap_settings_after=$(nmcli -g \
    connection.interface-name,connection.autoconnect,802-11-wireless.mode,802-11-wireless.ap-isolation,ipv4.method,ipv4.addresses,ipv4.shared-dhcp-range,ipv6.method,802-11-wireless-security.key-mgmt,802-11-wireless-security.proto,802-11-wireless-security.pairwise,802-11-wireless-security.group,802-11-wireless-security.pmf \
    connection show jittersim-ap 9>&-)

dashboard_ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (p = 1; p <= NF; p++) if ($p == "src") {print $(p + 1); exit}}' || true)
if [ -z "$dashboard_ip" ]; then
    dashboard_ip=127.0.0.1
fi
# The dashboard certificate comes from a small local certificate authority that is
# created once and kept across upgrades and rollbacks (the recovery copy holds the
# current verified identity is retained separately during recovery). Devices that
# trust the authority open the dashboard without a warning. Fresh certificates use
# neutral addresses. Existing valid aliases stay usable. The test Wi-Fi certificate
# contains only neutral names and the gateway.
# The same two commands run in the dashboard update. The first keeps the name
# setting and the resolver record. The second keeps the authority and renews the
# certificates. Only root reads the authority key.
"$INSTALL_DIR/.venv/bin/python" -m jittersim.localca recover-adoption --config-dir "$CONFIG_DIR" >&2
dns_name_state=$("$INSTALL_DIR/.venv/bin/python" -m jittersim.localname install \
    --config-dir "$CONFIG_DIR" --group jittersim)
local_name=$(printf '%s\n' "$dns_name_state" | sed -n 1p)
dns_name_changed=no
if [ "$(printf '%s\n' "$dns_name_state" | sed -n 2p)" = changed ]; then
    dns_name_changed=yes
fi
dashboard_host="$(hostname -s).local"
"$INSTALL_DIR/.venv/bin/python" -m jittersim.localca ensure \
    --config-dir "$CONFIG_DIR" --host-name "$dashboard_host" \
    --neutral-name "$local_name" --ethernet-ip "$dashboard_ip" --group jittersim >&2

# The one neutral dashboard name is announced with mDNS on Ethernet and on the test
# Wi-Fi, so the control firewall below lets mDNS in on the test Wi-Fi. Avahi cannot
# limit its own publishing to one network. It is therefore limited first and
# publishes nothing by itself (no host name, no services). The name publisher adds
# one record per network. This is optional. A failure leaves the dashboard open by its
# IP address, and the name publisher is then not started.
prepare_dashboard_name_service() {
    apt-get install -y avahi-daemon avahi-utils python3-dbus || return 1
    "$INSTALL_DIR/.venv/bin/python" -m jittersim.mdnsname configure \
        --uplink "$JITTERSIM_UPLINK" --test-interface "$JITTERSIM_TEST_INTERFACE" || return 1
    systemctl restart avahi-daemon.service || return 1
}
dashboard_name_ready=yes
if ! prepare_dashboard_name_service; then
    dashboard_name_ready=no
    echo "Warning: Avahi could not be prepared for the dashboard name. Use the Pi's Ethernet address." >&2
    echo "Avahi is stopped so it cannot announce the Pi on the test Wi-Fi with its own settings." >&2
    systemctl stop avahi-daemon.service avahi-daemon.socket || true
fi

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
# Keep this compatibility hook across a rollback to an older dashboard unit.
install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/homepage-profile-compat.py" \
    /usr/local/lib/jittersim-profile-compat.py
install -d -m 0755 -o root -g root /etc/systemd/system/jittersim-dashboard.service.d
install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/homepage-compat.conf" \
    /etc/systemd/system/jittersim-dashboard.service.d/homepage-compat.conf

install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/jittersim-browser-proxy.service" \
    /etc/systemd/system/jittersim-browser-proxy.service
install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/jittersim-service-watch.service" \
    /etc/systemd/system/jittersim-service-watch.service
install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/jittersim-adblocking.service" \
    /etc/systemd/system/jittersim-adblocking.service

install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/jittersim-update.service" \
    /etc/systemd/system/jittersim-update.service
install -m 0644 -o root -g root \
    "$SOURCE_DIR/deploy/raspberry-pi/jittersim-update-fetch.service" \
    /etc/systemd/system/jittersim-update-fetch.service

# Recovery must run even if an interrupted package install damages the venv.
install -d -m 0755 -o root -g root /usr/local/lib
python3 -I "$SOURCE_DIR/src/jittersim/updates.py" bootstrap-manager
systemctl enable jittersim-update.service
# The update manager writes the sudoers rule itself and checks it with visudo first.
if [ "$enable_passwordless_staging" = yes ]; then
    python3 -I /usr/local/lib/jittersim-update.py enable-passwordless-staging
fi

for access_service in jittersim-dns jittersim-access-boot jittersim-ap; do
    install -m 0644 -o root -g root "$SOURCE_DIR/deploy/raspberry-pi/$access_service.service" \
        "/etc/systemd/system/$access_service.service"
done
# An older main install kept household access state here. On an upgrade from a
# release without access rules nothing in the database knows about it, so it is
# moved to a dated folder that only root can read instead of being restored.
archive_stale_access_state() {
    if [ "$previous_access_release" = yes ] || [ ! -d /var/lib/jittersim-access ]; then
        return 0
    fi
    if [ -z "$(ls -A /var/lib/jittersim-access)" ]; then
        return 0
    fi
    access_backup="/var/lib/jittersim-access-before-$(date +%Y%m%d-%H%M%S)"
    mv /var/lib/jittersim-access "$access_backup"
    chown -R root:root "$access_backup"
    chmod 0700 "$access_backup"
    echo "Moved old household access state to $access_backup (not restored)." >&2
}
archive_stale_access_state
install -d -m 0750 -o root -g jittersim /var/lib/jittersim-access
systemctl daemon-reload
systemctl enable jittersim-access-boot.service jittersim-ap.service
# A failed restore keeps test traffic closed behind the guard table (sign-in and DHCP
# stay open). The installer carries on so the new helper starts and Normal access
# (`sudo jittersim normal-access` or Panic) can open the test Wi-Fi again.
restore_access_rules() {
    if ! systemctl restart jittersim-access-boot.service; then
        echo "Warning: the saved household access rules could not be restored." >&2
        echo "Test Wi-Fi internet access stays closed until you run: sudo jittersim normal-access" >&2
    fi
}
restore_access_rules
systemctl enable jittersim-control-firewall.service
systemctl restart jittersim-control-firewall.service
systemctl enable jittersim-helper.service jittersim-dashboard.service jittersim-browser-proxy.service jittersim-adblocking.service jittersim-service-watch.service
systemctl restart jittersim-helper.service
if [ "$ap_active_connection" = jittersim-ap ]; then
    if [ "$ap_settings_before" != "$ap_settings_after" ] || [ "$dns_name_changed" = yes ]; then
        echo "AP settings were saved but the active Wi-Fi was not restarted." >&2
        echo "To use them now, run: sudo nmcli connection up jittersim-ap" >&2
        echo "Test Wi-Fi devices reconnect after a few seconds. A reboot also works." >&2
    fi
else
    systemctl start jittersim-ap.service
fi
systemctl restart jittersim-browser-proxy.service
systemctl restart jittersim-service-watch.service
systemctl restart jittersim-adblocking.service
systemctl restart jittersim-dashboard.service

setup_dashboard_name_service() {
    install -d -m 0755 -o root -g root /etc/NetworkManager/dispatcher.d || return 1
    install -m 0644 -o root -g root \
        "$SOURCE_DIR/deploy/raspberry-pi/jittersim-mdns.service" \
        /etc/systemd/system/jittersim-mdns.service || return 1
    install -m 0755 -o root -g root \
        "$SOURCE_DIR/deploy/raspberry-pi/jittersim-mdns-dispatcher" \
        /etc/NetworkManager/dispatcher.d/80-jittersim-mdns || return 1
    systemctl daemon-reload || return 1
    systemctl enable jittersim-mdns.service || return 1
    systemctl restart jittersim-mdns.service || return 1
}

if [ "$dashboard_name_ready" = yes ]; then
    if setup_dashboard_name_service; then
        echo "Dashboard name publisher started. Check it with: sudo jittersim diagnostics" >&2
    else
        echo "Warning: local dashboard name setup failed. Use the Pi's Ethernet address." >&2
    fi
fi

adoption_finished=$("$INSTALL_DIR/.venv/bin/python" -m jittersim.localca finish-adoption --config-dir "$CONFIG_DIR")
if [ "$adoption_finished" = completed ]; then
    # Complete the original candidate before renewing an address that changed
    # while installation was interrupted. The authority and valid key stay.
    "$INSTALL_DIR/.venv/bin/python" -m jittersim.localca ensure \
        --config-dir "$CONFIG_DIR" --host-name "$dashboard_host" \
        --neutral-name "$local_name" --ethernet-ip "$dashboard_ip" --group jittersim >&2
    systemctl restart jittersim-dashboard.service
fi
"$INSTALL_DIR/.venv/bin/python" -m jittersim.localca check-health --config-dir "$CONFIG_DIR" >&2

echo "Installed JitterSim $("$INSTALL_DIR/.venv/bin/python" -c 'import jittersim; print(jittersim.__version__)')" >&2
echo "JitterSim installed. Read the generated credentials locally from:" >&2
echo "  $CONFIG_DIR/control.env" >&2
echo "  $CONFIG_DIR/ap.env" >&2
echo "Dashboard address, the same on the router network and the test Wi-Fi: https://$local_name:8080" >&2
echo "Certificate authority fingerprint (verify before trusting):" >&2
openssl x509 -in "$CONFIG_DIR/ca/ca.crt" -noout -fingerprint -sha256 >&2
echo "After signing in, the dashboard offers this certificate to trust on a Mac or iPhone." >&2
