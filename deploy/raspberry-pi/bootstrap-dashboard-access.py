#!/usr/bin/env python3
"""Create the saved access list without replacing existing permissions."""

import grp

from jittersim.networking.dashboard_access import ACCESS_FILE, AccessStore


if __name__ == "__main__":
    AccessStore(ACCESS_FILE, group_id=grp.getgrnam("jittersim").gr_gid).initialize()
