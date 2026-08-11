/*
 * webhid_trace.js -- Mitschnitt der WebHID-Kommunikation der IO Center Web-App.
 *
 * Zweck: herausfinden, mit welchen Reports die offizielle Software Bilder,
 * Beleuchtung und Systemdaten uebertraegt -- um dieselben Funktionen unter
 * Linux nachzubauen (Interoperabilitaet).
 *
 * Das Snippet HOERT NUR MIT. Es ruft von sich aus kein sendReport() auf und
 * veraendert keine uebertragenen Daten -- jeder Aufruf wird unveraendert an
 * die Originalfunktion durchgereicht. Es kann die Tastatur nicht verstellen
 * und keine Firmware ausloesen.
 *
 * VERWENDUNG
 *   1. https://iocenter.bequiet.com/ oeffnen, Tastatur verbinden lassen
 *   2. DevTools oeffnen (F12) -> Reiter "Console"
 *   3. Diesen gesamten Text einfuegen, Enter
 *   4. In der Web-App die interessante Aktion ausfuehren
 *   5. In der Console:  bqTrace.save()     -> Datei wird heruntergeladen
 *      Zwischendurch:   bqTrace.summary()  -> Uebersicht im Log
 */

(() => {
  if (window.bqTrace) {
    console.warn("bqTrace läuft bereits. bqTrace.reset() zum Neustart.");
    return;
  }

  const t0 = performance.now();
  const events = [];
  const MAX = 20000;

  const hex = (view) => {
    const bytes = view instanceof DataView
      ? new Uint8Array(view.buffer, view.byteOffset, view.byteLength)
      : new Uint8Array(view.buffer || view);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(" ");
  };

  const record = (dir, kind, reportId, data, extra) => {
    if (events.length >= MAX) return;
    events.push({
      ms: Math.round(performance.now() - t0),
      dir, kind,
      id: reportId,
      len: data ? (data.byteLength ?? data.length) : 0,
      data: data ? hex(data) : "",
      ...(extra || {}),
    });
  };

  const proto = HIDDevice.prototype;
  const original = {
    sendReport: proto.sendReport,
    sendFeatureReport: proto.sendFeatureReport,
    receiveFeatureReport: proto.receiveFeatureReport,
  };

  proto.sendReport = function (reportId, data) {
    record("OUT", "output", reportId, data);
    return original.sendReport.apply(this, arguments);
  };

  proto.sendFeatureReport = function (reportId, data) {
    record("OUT", "feature", reportId, data);
    return original.sendFeatureReport.apply(this, arguments);
  };

  proto.receiveFeatureReport = function (reportId) {
    return original.receiveFeatureReport.apply(this, arguments).then((view) => {
      record("IN", "feature", reportId, view);
      return view;
    });
  };

  // Eingehende Reports (inputreport) an allen bereits erlaubten Geraeten.
  const attached = new WeakSet();
  const attach = (device) => {
    if (attached.has(device)) return;
    attached.add(device);
    device.addEventListener("inputreport", (event) => {
      record("IN", "input", event.reportId, event.data,
             { dev: device.productName });
    });
    console.log("bqTrace: lauscht an", device.productName);
  };

  navigator.hid.getDevices().then((devices) => {
    devices.forEach(attach);
    if (!devices.length) {
      console.warn("bqTrace: noch kein Gerät verbunden — in der Web-App "
                 + "verbinden, danach bqTrace.rescan() aufrufen.");
    }
  });

  const originalRequest = navigator.hid.requestDevice.bind(navigator.hid);
  navigator.hid.requestDevice = function (...args) {
    return originalRequest(...args).then((devices) => {
      devices.forEach(attach);
      return devices;
    });
  };

  window.bqTrace = {
    events,

    rescan: () => navigator.hid.getDevices().then((d) => {
      d.forEach(attach);
      return d.length;
    }),

    summary() {
      const groups = new Map();
      for (const e of events) {
        // Erste beiden Bytes als grobe Nachrichtenkennung.
        const tag = `${e.dir} ${e.kind} id=${e.id} [${e.data.slice(0, 5)}]`;
        const g = groups.get(tag) || { n: 0, lens: new Set(), first: e.data };
        g.n += 1;
        g.lens.add(e.len);
        groups.set(tag, g);
      }
      console.table([...groups].map(([tag, g]) => ({
        Nachricht: tag,
        Anzahl: g.n,
        Längen: [...g.lens].join(","),
        "Erste Bytes": g.first.slice(0, 47),
      })));
      console.log(`bqTrace: ${events.length} Ereignisse insgesamt.`);
    },

    save(name = "bq-trace.json") {
      const blob = new Blob([JSON.stringify(events, null, 1)],
                            { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      console.log(`bqTrace: ${events.length} Ereignisse in ${name} gespeichert.`);
    },

    reset() {
      events.length = 0;
      console.log("bqTrace: Mitschnitt geleert.");
    },

    stop() {
      Object.assign(proto, original);
      navigator.hid.requestDevice = originalRequest;
      delete window.bqTrace;
      console.log("bqTrace: beendet, Originalfunktionen wiederhergestellt.");
    },
  };

  console.log("%cbqTrace aktiv", "font-weight:bold",
              "— nur mitlesen. bqTrace.summary() / bqTrace.save() / bqTrace.stop()");
})();
