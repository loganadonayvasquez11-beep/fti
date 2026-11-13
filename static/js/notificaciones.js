(() => {
  const button = document.getElementById("activar-notificaciones");
  const status = document.getElementById("estado-notificaciones");
  if (!button || !status) return;

  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  let audio;
  let sonidoActivo = false;
  let pushActivo = false;
  let mensajePush = "";
  let hayPendientes = false;
  let intervaloSonido;

  function sonar() {
    if (!audio || audio.state !== "running") return;
    const ahora = audio.currentTime;
    [0, 0.3, 0.6].forEach((retraso, i) => {
      const tono = audio.createOscillator();
      const volumen = audio.createGain();
      tono.type = "square";
      tono.frequency.value = i === 1 ? 1175 : 880;
      volumen.gain.setValueAtTime(0.001, ahora + retraso);
      volumen.gain.exponentialRampToValueAtTime(0.18, ahora + retraso + 0.02);
      volumen.gain.exponentialRampToValueAtTime(0.001, ahora + retraso + 0.2);
      tono.connect(volumen);
      volumen.connect(audio.destination);
      tono.start(ahora + retraso);
      tono.stop(ahora + retraso + 0.21);
    });
  }

  async function revisarPendientes() {
    try {
      const response = await fetch("/notificaciones/pendientes", {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      hayPendientes = data.tasks.length > 0;
      if (hayPendientes) {
        status.textContent = [
          `${data.tasks.length} pendiente(s); la alarma se repite cada 3 segundos.`,
          mensajePush,
        ].filter(Boolean).join(" ");
        if (sonidoActivo && !intervaloSonido) {
          sonar();
          intervaloSonido = window.setInterval(sonar, data.interval || 3000);
        }
      } else {
        window.clearInterval(intervaloSonido);
        intervaloSonido = undefined;
        status.textContent = [
          pushActivo ? "Avisos push activados." : "",
          sonidoActivo ? "Sonido activado." : "",
          hayPendientes ? "" : "No hay pedidos pendientes.",
          mensajePush,
        ].filter(Boolean).join(" ");
      }
    } catch (error) {
      status.textContent = "No se pudo consultar la bandeja. Revisa tu conexión.";
      console.error("Error al consultar notificaciones pendientes:", error);
    }
  }

  async function suscribirPush(registration, publicKey) {
    const existente = await registration.pushManager.getSubscription();
    const subscription = existente || await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: Uint8Array.from(
        atob(publicKey.replace(/-/g, "+").replace(/_/g, "/")
          + "=".repeat((4 - publicKey.length % 4) % 4)),
        (character) => character.charCodeAt(0),
      ),
    });
    const response = await fetch("/notificaciones/suscribir", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ subscription: subscription.toJSON() }),
    });
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.error || `HTTP ${response.status}`);
    }
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      let audioReady;
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (AudioContext) {
        audio ||= new AudioContext();
        audioReady = audio.resume();
        sonidoActivo = true;
        audioReady.then(sonar);
      }
      if (!("serviceWorker" in navigator) || !("Notification" in window)) {
        mensajePush = "Este navegador no admite notificaciones push.";
        await audioReady;
        await revisarPendientes();
        return;
      }

      const permissionPromise = Notification.permission === "default"
        ? Notification.requestPermission()
        : Promise.resolve(Notification.permission);
      const registration = await navigator.serviceWorker.register("/service-worker.js");
      const permission = await permissionPromise;
      await audioReady;
      if (permission !== "granted") {
        mensajePush = "Permite las notificaciones en los ajustes del navegador para recibir avisos push.";
        await revisarPendientes();
        return;
      }

      const configResponse = await fetch("/notificaciones/config", {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!configResponse.ok) throw new Error(`HTTP ${configResponse.status}`);
      const config = await configResponse.json();
      if (!config.pushEnabled || !config.publicKey) {
        mensajePush = "Web Push aún no está configurado en el servidor.";
        await revisarPendientes();
        return;
      }
      await suscribirPush(registration, config.publicKey);
      pushActivo = true;
      mensajePush = "";
      await revisarPendientes();
    } catch (error) {
      status.textContent = `No se pudieron activar los avisos: ${error.message}`;
      console.error("Error al activar notificaciones:", error);
    } finally {
      button.disabled = false;
    }
  });

  revisarPendientes();
  window.setInterval(revisarPendientes, 3000);
})();
