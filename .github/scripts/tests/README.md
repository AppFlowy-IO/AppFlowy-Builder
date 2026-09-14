# Linux packaging regression checks

The Linux workflow builds x86_64 and ARM64 on native Ubuntu 22.04 runners and
validates the **final packages** before uploading release assets. Debian
installation runs in clean Ubuntu 22.04, 24.04 and 26.04 containers on matching
native runners. Tarball and AppImage startup run on Ubuntu 24.04 for both
architectures without an installed mpv. All launch checks require a visible, nonblank
AppFlowy window that stays alive; their logs and PNG screenshots are retained.

Use the matching frontend change that emits `host-libraries.regex` in
the CMake bundle. That manifest combines the explicit host runtime policy and
the build machine's GTK dependency closure. The builder audits it at each
packaging boundary; it must not download another copy of mpv or the host C++
runtime while making an archive.

`linux_package.py depends` stages the complete payload as a private Debian
package, records bundled SONAMEs as owned by AppFlowy, and lets
`dpkg-shlibdeps` calculate the remaining external dependencies. Versioned
libraries use local shlibs metadata; unversioned Flutter plugins use private
symbols metadata. Missing dependency information is an error. Dependencies
loaded by name at runtime (desktop integration and GStreamer) remain explicit.

ELF headers determine the package architecture. The audit rejects mixed CPU
architectures and can require a specific target with `--architecture amd64`
or `--architecture arm64`. Dependency calculation requires a matching native
Debian host and updates both `Architecture` and `Depends` in the control file.
The fixture checks the resulting `.deb` architecture without modifying the
frontend's default `amd64` control template beforehand.

## Reproduce the native dependency and EGL regressions

Build the frontend's native probe on Ubuntu 22.04 with its production CMake
rules (see `frontend/appflowy_flutter/linux/test/README.md` in the source
repo). This needs no Flutter/Rust build. On that same build system, with
`python3-yaml`, `dpkg-dev` and `binutils` installed:

```sh
python3 .github/scripts/tests/build_linux_deb_fixture.py \
  --frontend /path/to/AppFlowy-Premium/frontend \
  --bundle /path/to/installed/egl/bundle \
  --output /tmp/appflowy-deb-fixture
python3 .github/scripts/tests/test_linux_package.py \
  --bundle /path/to/installed/egl/bundle
```

The fixture executes the workflow's actual dependency step and the frontend's
actual Debian packager. In a **disposable runtime container**, install Xvfb,
xauth and Mesa (`libegl1 libegl-mesa0 libgl1-mesa-dri`), update APT, then run
as root:

```sh
bash .github/scripts/tests/install_linux_deb_fixture.sh \
  /tmp/appflowy-deb-fixture/appflowy-packaging-fixture.deb
```

Ubuntu 24.04/26.04 must install the package from their normal repositories and
initialize EGL with the bundled mpv. The old dependency step instead requires
Ubuntu 22.04 codec packages such as libavcodec58/libmpv1. The old CMake rules
bundle libstdc++, which can prevent the newer host Mesa/LLVM from loading.

Run the fixture on each native architecture. The bundle audit checks also
reject an incorrect target architecture and a private ELF with a foreign
machine header. The full workflow runs these negative controls against each
built application bundle before packaging.

## Exercise the smoke gate itself

With a C compiler and GTK development files, build the real GTK fixture:

```sh
cc -Wall -Wextra -Werror .github/scripts/tests/smoke_window.c \
  -o /tmp/appflowy-smoke-window $(pkg-config --cflags --libs gtk+-3.0)
python3 .github/scripts/tests/test_linux_smoke.py \
  --fixture /tmp/appflowy-smoke-window
```

The runtime requires `python3-gi gir1.2-gtk-3.0 xvfb xauth xdotool`.
The check accepts a rendered window and rejects a process that aborts after
drawing, exits early with status zero, shows a blank window, or never maps a
window.

To run it against an actual extracted/installed AppFlowy:

```sh
xvfb-run -a python3 .github/scripts/linux_smoke.py \
  --log /tmp/appflowy-smoke.log /path/to/AppFlowy
```

A successful screenshot establishes startup rendering, not document editing
or media playback. For release verification, also open a document containing
video, play/pause/seek it, and restart the installed app on the reported
Arch/AMD system in both Wayland and X11 sessions.
