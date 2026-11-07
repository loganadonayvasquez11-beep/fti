const TIME_ZONE = "America/Caracas";
const HANDLER_NAME = "enviarHistoricoDiario";

function enviarHistoricoDiario() {
  const props = PropertiesService.getScriptProperties();
  const appUrl = props.getProperty("APP_URL");
  const destinatario = props.getProperty("DESTINO_GMAIL");
  const token = props.getProperty("DAILY_ARCHIVE_TOKEN");

  if (!appUrl || !destinatario || !token) {
    throw new Error("Configura APP_URL, DESTINO_GMAIL y DAILY_ARCHIVE_TOKEN en las propiedades del script.");
  }
  if (!/^https:\/\//i.test(appUrl)) {
    throw new Error("APP_URL debe usar HTTPS.");
  }

  const fecha = fechaReporteAyer();
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    throw new Error("Ya hay otro envío del histórico en ejecución.");
  }

  try {
    if (props.getProperty("ULTIMO_HISTORICO_ENVIADO") === fecha) {
      console.log("El histórico de " + fecha + " ya fue enviado.");
      return;
    }

    const url = appUrl.replace(/\/+$/, "") + "/integraciones/historico-diario.pdf";
    const respuesta = UrlFetchApp.fetch(url, {
      method: "get",
      headers: { "X-Archive-Token": token },
      muteHttpExceptions: true
    });
    if (respuesta.getResponseCode() !== 200) {
      throw new Error("La aplicación respondió HTTP " + respuesta.getResponseCode() + ".");
    }
    const bytes = respuesta.getContent();
    const firmaPdf = String.fromCharCode.apply(null, bytes.slice(0, 5));
    if (firmaPdf !== "%PDF-") {
      throw new Error("La respuesta de la aplicación no contiene un PDF válido.");
    }

    const nombre = "Historico diario - " + fechaEnFormato(fecha, "dd-MM-yyyy") + ".pdf";
    const adjunto = respuesta.getBlob().setName(nombre);
    MailApp.sendEmail({
      to: destinatario,
      subject: "Histórico diario - " + fechaEnFormato(fecha, "dd-MM-yyyy"),
      body: "Adjunto encontrarás el histórico diario correspondiente al " +
        fechaEnFormato(fecha, "dd-MM-yyyy") + ".",
      attachments: [adjunto]
    });
    props.setProperty("ULTIMO_HISTORICO_ENVIADO", fecha);
  } finally {
    lock.releaseLock();
  }
}

function instalarEnvioHistoricoDiario() {
  const props = PropertiesService.getScriptProperties();
  ["APP_URL", "DESTINO_GMAIL", "DAILY_ARCHIVE_TOKEN"].forEach(function (key) {
    if (!props.getProperty(key)) {
      throw new Error("Falta configurar la propiedad del script: " + key);
    }
  });

  ScriptApp.getProjectTriggers()
    .filter(function (trigger) {
      return trigger.getHandlerFunction() === HANDLER_NAME;
    })
    .forEach(function (trigger) {
      ScriptApp.deleteTrigger(trigger);
    });

  ScriptApp.newTrigger(HANDLER_NAME)
    .timeBased()
    .atHour(1)
    .nearMinute(10)
    .everyDays(1)
    .inTimezone(TIME_ZONE)
    .create();
}

function fechaReporteAyer() {
  const hoyLocal = Utilities.formatDate(new Date(), TIME_ZONE, "yyyy-MM-dd").split("-");
  const ayer = new Date(Date.UTC(Number(hoyLocal[0]), Number(hoyLocal[1]) - 1, Number(hoyLocal[2])) - 86400000);
  return Utilities.formatDate(ayer, "UTC", "yyyy-MM-dd");
}

function fechaEnFormato(fecha, formato) {
  const partes = fecha.split("-");
  const fechaUtc = new Date(Date.UTC(Number(partes[0]), Number(partes[1]) - 1, Number(partes[2])));
  return Utilities.formatDate(fechaUtc, "UTC", formato);
}
