#!/bin/sh
set -eu

RUNTIME_CONFIG=/etc/jittersim/runtime.env
if [ ! -r "$RUNTIME_CONFIG" ]; then
    echo "JitterSim runtime configuration is unavailable" >&2
    exit 1
fi
. "$RUNTIME_CONFIG"

uplink=${JITTERSIM_UPLINK:-eth0}
avahi_config=/etc/avahi/avahi-daemon.conf
if [ -r "$avahi_config" ]; then
    allowed_interfaces=$(awk -F= '
        /^\[server\][[:space:]]*$/ {in_server=1; next}
        /^\[/ {in_server=0}
        in_server && $1 ~ /^[[:space:]]*allow-interfaces[[:space:]]*$/ {
            value=$2
            sub(/^[[:space:]]*/, "", value)
            sub(/[[:space:]]*$/, "", value)
            print value
            exit
        }
    ' "$avahi_config" | tr -d '[:space:]')
    denied_interfaces=$(awk -F= '
        /^\[server\][[:space:]]*$/ {in_server=1; next}
        /^\[/ {in_server=0}
        in_server && $1 ~ /^[[:space:]]*deny-interfaces[[:space:]]*$/ {
            value=$2
            sub(/^[[:space:]]*/, "", value)
            sub(/[[:space:]]*$/, "", value)
            print value
            exit
        }
    ' "$avahi_config" | tr -d '[:space:]')
    if [ -n "$allowed_interfaces" ]; then
        case ",$allowed_interfaces," in
            *,"$uplink",*) ;;
            *) echo "Avahi is not configured to use management interface $uplink" >&2; exit 1 ;;
        esac
    fi
    case ",$denied_interfaces," in
        *,"$uplink",*)
            echo "Avahi is configured to ignore management interface $uplink" >&2
            exit 1
            ;;
    esac
fi

dashboard_ip=$(ip -4 route get 1.1.1.1 oif "$uplink" 2>/dev/null \
    | awk '{for (i = 1; i <= NF; i++) if ($i == "src") {print $(i + 1); exit}}')
if [ -z "$dashboard_ip" ] || [ "$dashboard_ip" = 127.0.0.1 ]; then
    echo "No management IPv4 address is available on $uplink" >&2
    exit 1
fi

exec /usr/bin/avahi-publish-address --no-fail jittersim.local "$dashboard_ip"
