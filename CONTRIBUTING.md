# Contributing

Thank you for your interest in IO Center for Linux. Bug reports, verified
device information, translations, and small, well-supported improvements are
welcome.

## Safety boundary

Device commands are accepted only when they are documented by at least one
traceable source and subsequently verified on real hardware with strict
bounds. Unknown commands, firmware or DFU functionality, and bootloader
operations do not belong in this project. A presumed meaning is not sufficient
evidence.

Every device command that writes data requires:

- an explicit allowlist;
- strict bounds for IDs, offsets, and payload sizes;
- a backup whenever existing user data is replaced;
- readback and verification wherever the protocol permits it;
- a test covering invalid or truncated responses.

## Local verification

```bash
python3 -m pip install --user -r requirements.txt
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v
python3 -m compileall -q -f .
desktop-file-validate data/io.github.re133.iocenterlinux.desktop
appstreamcli validate --no-net data/io.github.re133.iocenterlinux.metainfo.xml
udevadm verify 70-iocenter-dark-mount.rules
```

Please document hardware tests with the model, firmware version, Linux
distribution, desktop environment, and exact result. Do not publish serial
numbers or personal key commands in issues or logs.

## Style

- Prefer small, reviewable changes.
- German is the source language of the interface; every new user-visible
  string needs an English entry in `bqi18n.py`.
- Do not use fixed `hidraw` numbers or home-directory paths.
- User data belongs exclusively in the XDG directories.
