Name:           iocenter-linux
Version:        0.1.0
Release:        1%{?dist}
Summary:        Linux control panel for the be quiet! Dark Mount

License:        GPL-3.0-only
URL:            https://github.com/re133/iocenter-linux
Source0:        %{url}/archive/refs/tags/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-build
BuildRequires:  python3-installer
BuildRequires:  python3-setuptools
BuildRequires:  python3-wheel
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib
BuildRequires:  systemd-udev
Requires:       python3-pyqt6 >= 6.6
Requires:       python3-pillow >= 10
Requires:       qt6-qtsvg
Requires:       systemd-udev

%description
An independent community control panel for the be quiet! Dark Mount keyboard.
It manages display-key images, Media Dock settings, lighting, Linux shortcuts,
and CPU/GPU load meters. Firmware operations are deliberately excluded.

%prep
%autosetup

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files bqconfig bqdevice bqdock bqgui bqi18n bqimage bqkeyd bqlamp bqlight bqlink bqmeta bqmeter bqpaths bqui

%check
QT_QPA_PLATFORM=offscreen %{python3} -m unittest discover -v
desktop-file-validate data/io.github.re133.iocenterlinux.desktop
appstream-util validate-relax --nonet data/io.github.re133.iocenterlinux.metainfo.xml
udevadm verify 70-iocenter-dark-mount.rules

%post
udevadm control --reload-rules >/dev/null 2>&1 || :

%postun
udevadm control --reload-rules >/dev/null 2>&1 || :

%files -f %{pyproject_files}
%license LICENSE
%doc README.md CHANGELOG.md DARKMOUNT_REVIEW.md
%{_bindir}/iocenter-*
%{_datadir}/applications/io.github.re133.iocenterlinux.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.re133.iocenterlinux.svg
%{_datadir}/metainfo/io.github.re133.iocenterlinux.metainfo.xml
%{_datadir}/iocenter-linux/
%{_prefix}/lib/udev/rules.d/70-iocenter-dark-mount.rules

%changelog
* Tue Aug 11 2026 re133 - 0.1.0-1
- Initial public preview
