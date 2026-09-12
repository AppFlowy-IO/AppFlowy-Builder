#!/usr/bin/env bash
# Run as root only inside a disposable, clean target-distribution container.
set -euo pipefail
package=$(realpath "$1")
apt-get install -y "$package"
test "$(dpkg-query -W -f='${Status}' appflowy)" = "install ok installed"
# Exercise the installed desktop entry point, including its case-sensitive name.
xvfb-run -a /usr/bin/AppFlowy
