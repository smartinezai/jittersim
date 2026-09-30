"""Install under a private caller mask then import as an ordinary service user."""

import os
import pwd
import shlex
import subprocess
import tempfile
from pathlib import Path


def main():
    if os.geteuid() != 0 or not Path("/.dockerenv").exists():
        raise RuntimeError("Run as root only in a disposable Linux container.")
    source = Path.cwd()
    startup = (source / "deploy/raspberry-pi/install.sh").read_text().split("SOURCE_DIR=", 1)[0]
    with tempfile.TemporaryDirectory(prefix="installer-permissions-") as directory:
        root = Path(directory)
        root.chmod(0o755)
        wheel_dir = root / "wheels"
        subprocess.run(["python", "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                        "--wheel-dir", str(wheel_dir), str(source)], check=True, timeout=120)
        wheel = next(wheel_dir.glob("jittersim-*.whl"))
        environment = root / "environment"
        script = startup + "\n" + "\n".join([
            "python -m venv --system-site-packages " + shlex.quote(str(environment)),
            shlex.quote(str(environment / "bin/pip")) + " install --no-deps --force-reinstall "
            + shlex.quote(str(wheel)),
        ])
        subprocess.run(["sh", "-c", script], check=True, timeout=120,
                       preexec_fn=lambda: os.umask(0o077))
        account = pwd.getpwnam("nobody")

        def use_service_permissions():
            os.setgroups([])
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)

        check = """
from pathlib import Path
import sys
from jittersim.api import server
from jittersim.networking import browser_proxy, helper
from jittersim import dashboard_access_cli
for module in (server, browser_proxy, helper, dashboard_access_cli):
    assert Path(sys.prefix) in Path(module.__file__).parents, module.__file__
print('Fresh installed service modules import with ordinary user permissions.')
"""
        subprocess.run([str(environment / "bin/python"), "-I", "-c", check],
                       check=True, timeout=30, cwd="/", preexec_fn=use_service_permissions)


if __name__ == "__main__":
    main()
