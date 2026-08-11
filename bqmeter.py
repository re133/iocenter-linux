#!/usr/bin/env python3
"""
bqmeter -- CPU-/GPU-Auslastung auf zwei Tastenreihen anzeigen.

    F1 .. F10   GPU, Stufen 10 .. 100 Prozent
    1  .. 0     CPU, Stufen 10 .. 100 Prozent

Standardmäßig leuchtet je Reihe nur die aktuelle Stufe ("dot"). Mit
"--mode bar" leuchten alle Stufen bis zum aktuellen Wert. Alle anderen LEDs
bleiben während des Monitors aus. Beim Beenden gibt das Programm die
LampArray-Steuerung an die Tastatur zurück, sodass der Onboard-Effekt wieder
erscheint.

Der Monitor benutzt ausschließlich den offenen HID-LampArray-Standard. Er
spricht weder den QLink-/Firmware-Kanal an noch schreibt er Geräte-Flash.
"""

import argparse
import glob
import math
import os
import signal
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bqlamp  # noqa: E402


OFF = (0, 0, 0)
DEFAULT_GPU_COLOUR = (255, 40, 0)
DEFAULT_CPU_COLOUR = (0, 145, 255)
MIN_INTERVAL = 0.25


def _longest_consecutive_run(lamps):
    """Längste Folge aufeinanderfolgender Lampen-IDs einer physischen Reihe."""
    ordered = sorted(lamps, key=lambda lamp: lamp.id)
    runs, current = [], []
    for lamp in ordered:
        if current and lamp.id != current[-1].id + 1:
            runs.append(current)
            current = []
        current.append(lamp)
    if current:
        runs.append(current)
    return max(runs, key=len) if runs else []


def meter_keys(lamps):
    """Ermittelt F1–F10 und 1–0 aus der gemeldeten Dark-Mount-Geometrie.

    Die Tastatur meldet sechs dicht belegte Tastenreihen. In den ersten
    beiden enthält der längste fortlaufende ID-Block jeweils den TKL-Teil
    einschließlich einer Randlampe links/rechts. Dazwischen liegen die Tasten
    in physischer Reihenfolge:

      Rand, Esc, F1..F12, Druck/Roll/Pause, Rand
      Rand, ^,   1..0,   ß/´/Back, Navigation, Rand

    Für andere LampArray-Geräte wird bewusst abgebrochen statt geraten.
    """
    lamps = list(lamps)
    if not lamps:
        raise ValueError("Das Gerät meldet keine Lampen.")

    by_y = {}
    for lamp in lamps:
        by_y.setdefault(lamp.y, []).append(lamp)
    ys = sorted(by_y)
    if len(ys) < 3:
        raise ValueError("Zu wenige physische Lampenreihen.")

    key_rows = [by_y[y] for y in ys[1:-1] if len(by_y[y]) >= 8]
    if len(key_rows) < 2:
        raise ValueError("Funktionstasten- und Zahlenreihe nicht gefunden.")

    function_run = _longest_consecutive_run(key_rows[0])
    number_run = _longest_consecutive_run(key_rows[1])
    if len(function_run) != 18 or len(number_run) != 19:
        raise ValueError(
            "Unbekanntes LampArray-Layout (Reihen %d/%d statt 18/19). "
            "Es werden keine IDs geraten."
            % (len(function_run), len(number_run)))

    function_ids = [lamp.id for lamp in function_run[2:12]]
    number_ids = [lamp.id for lamp in number_run[2:12]]
    if len(function_ids) != 10 or len(number_ids) != 10:
        raise AssertionError("Interner Fehler bei der Tastenbelegung.")
    return function_ids, number_ids


def level(load):
    """0 Prozent -> 0; sonst auf die Stufe 1..10 aufrunden."""
    load = max(0.0, min(100.0, float(load)))
    return 0 if load == 0 else min(10, int(math.ceil(load / 10.0)))


def row_colours(key_ids, load, colour, mode="dot"):
    """Vollständiges Update einer Zehnerreihe erzeugen."""
    if mode not in ("dot", "bar"):
        raise ValueError("Unbekannter Anzeigemodus %r" % mode)
    active = level(load)
    items = []
    for index, lamp_id in enumerate(key_ids, 1):
        lit = index == active if mode == "dot" else index <= active
        items.append((lamp_id, colour if lit else OFF))
    return items


def read_cpu_times(path="/proc/stat"):
    with open(path) as fh:
        fields = fh.readline().split()
    if not fields or fields[0] != "cpu" or len(fields) < 5:
        raise RuntimeError("CPU-Zeile in %s nicht lesbar." % path)
    values = [int(value) for value in fields[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    # guest/guest_nice sind bereits in user/nice enthalten und dürfen nicht
    # ein zweites Mal in die Gesamtsumme eingehen.
    return sum(values[:8]), idle


class CpuSampler:
    def __init__(self, path="/proc/stat"):
        self.path = path
        self.previous = read_cpu_times(path)

    def sample(self):
        current = read_cpu_times(self.path)
        total_delta = current[0] - self.previous[0]
        idle_delta = current[1] - self.previous[1]
        self.previous = current
        if total_delta <= 0:
            return 0.0
        return max(0.0, min(100.0,
                            100.0 * (total_delta - idle_delta) / total_delta))


class GpuSampler:
    """AMD über sysfs, sonst NVIDIA über nvidia-smi."""

    def __init__(self):
        self.paths = [path for path in sorted(glob.glob(
            "/sys/class/drm/card*/device/gpu_busy_percent"))
                      if os.access(path, os.R_OK)]
        self.nvidia_smi = shutil.which("nvidia-smi") if not self.paths else None
        if self.paths:
            self.source = ", ".join(self.paths)
        elif self.nvidia_smi:
            self.source = self.nvidia_smi
        else:
            raise RuntimeError(
                "Keine GPU-Auslastungsquelle gefunden (AMD sysfs oder "
                "nvidia-smi).")

    def sample(self):
        values = []
        if self.paths:
            for path in self.paths:
                try:
                    with open(path) as fh:
                        values.append(float(fh.read().strip()))
                except (OSError, ValueError):
                    continue
        else:
            try:
                result = subprocess.run(
                    [self.nvidia_smi, "--query-gpu=utilization.gpu",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=2, check=True)
                values = [float(line.strip())
                          for line in result.stdout.splitlines()
                          if line.strip()]
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                raise RuntimeError("GPU-Auslastung nicht lesbar: %s" % exc)
        if not values:
            raise RuntimeError("GPU-Auslastung nicht lesbar.")
        return max(0.0, min(100.0, max(values)))


class LoadMeter:
    def __init__(self, interval=1.0, mode="dot", swap=False,
                 gpu_colour=DEFAULT_GPU_COLOUR,
                 cpu_colour=DEFAULT_CPU_COLOUR):
        self.interval = max(MIN_INTERVAL, float(interval))
        self.mode = mode
        self.swap = bool(swap)
        self.gpu_colour = gpu_colour
        self.cpu_colour = cpu_colour

    def run(self, stop_event=None, on_ready=None, on_sample=None,
            max_samples=0):
        stop_event = stop_event or threading.Event()
        gpu = GpuSampler()       # erst Quellen prüfen, dann LEDs übernehmen
        cpu = CpuSampler()
        array = bqlamp.LampArray()
        samples = 0
        try:
            gpu_keys, cpu_keys = meter_keys(array.lamps())
            if self.swap:
                gpu_keys, cpu_keys = cpu_keys, gpu_keys
            if on_ready:
                on_ready(array.path, gpu.source, gpu_keys, cpu_keys)

            array.take_control()
            array.solid(OFF)

            # Für die CPU-Prozentzahl wird ein Delta benötigt.
            if stop_event.wait(self.interval):
                return

            while not stop_event.is_set():
                gpu_load = gpu.sample()
                cpu_load = cpu.sample()
                items = row_colours(
                    gpu_keys, gpu_load, self.gpu_colour, self.mode)
                items += row_colours(
                    cpu_keys, cpu_load, self.cpu_colour, self.mode)
                array.set_lamps(items)
                samples += 1
                if on_sample:
                    on_sample(gpu_load, cpu_load)
                if max_samples and samples >= max_samples:
                    break
                if stop_event.wait(self.interval):
                    break
        finally:
            array.close()       # gibt AutonomousMode zuverlässig zurück


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interval", type=float, default=1.0, metavar="SEK",
                        help="Messintervall, mindestens %.2f s" % MIN_INTERVAL)
    parser.add_argument("--mode", choices=("dot", "bar"), default="dot",
                        help="nur aktuelle Stufe oder gefüllter Balken")
    parser.add_argument("--swap", action="store_true",
                        help="GPU auf 1–0 und CPU auf F1–F10 anzeigen")
    parser.add_argument("--gpu-color", default="#ff2800", metavar="#RRGGBB")
    parser.add_argument("--cpu-color", default="#0091ff", metavar="#RRGGBB")
    parser.add_argument("--samples", type=int, default=0, metavar="N",
                        help="nach N Messungen beenden (0 = bis Ctrl-C)")
    parser.add_argument("--show-keys", action="store_true",
                        help="ermittelte Lampen-IDs anzeigen und beenden")
    args = parser.parse_args()

    if args.show_keys:
        array = bqlamp.LampArray()
        try:
            gpu_keys, cpu_keys = meter_keys(array.lamps())
            print("GPU F1..F10:", " ".join(map(str, gpu_keys)))
            print("CPU 1..0:    ", " ".join(map(str, cpu_keys)))
        finally:
            array.close()
        return

    meter = LoadMeter(
        interval=args.interval,
        mode=args.mode,
        swap=args.swap,
        gpu_colour=bqlamp.parse_colour(args.gpu_color),
        cpu_colour=bqlamp.parse_colour(args.cpu_color))

    def ready(path, source, gpu_keys, cpu_keys):
        print("LampArray: %s" % path)
        print("GPU-Quelle: %s" % source)
        print("GPU %s: %s" % ("1..0" if args.swap else "F1..F10",
                              " ".join(map(str, gpu_keys))))
        print("CPU %s: %s" % ("F1..F10" if args.swap else "1..0",
                              " ".join(map(str, cpu_keys))))
        print("Beenden mit Ctrl-C; der Onboard-Effekt wird wiederhergestellt.")

    def sample(gpu_load, cpu_load):
        print("\rGPU %5.1f %%  ·  CPU %5.1f %%" % (gpu_load, cpu_load),
              end="", flush=True)

    stop_event = threading.Event()

    def stop(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, stop)

    try:
        meter.run(stop_event=stop_event, on_ready=ready, on_sample=sample,
                  max_samples=max(0, args.samples))
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, RuntimeError, SystemExit) as exc:
        raise SystemExit("Fehler: %s" % exc)
    finally:
        print()


if __name__ == "__main__":
    main()
