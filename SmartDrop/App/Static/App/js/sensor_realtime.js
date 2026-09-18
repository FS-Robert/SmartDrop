(function () {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socketUrl = `${protocol}//${window.location.host}/ws/sensors/`;
  let socket;
  let retryDelay = 1000;
  let retryTimer;

  function connect() {
    socket = new WebSocket(socketUrl);

    socket.addEventListener('open', function () {
      retryDelay = 1000;
      window.dispatchEvent(new CustomEvent('smartdrop:realtime-status', {
        detail: { connected: true },
      }));
    });

    socket.addEventListener('message', function (event) {
      try {
        const payload = JSON.parse(event.data);
        if (payload.event === 'sensor_reading') {
          window.dispatchEvent(new CustomEvent('smartdrop:sensor-reading', {
            detail: payload,
          }));
        }
      } catch (error) {
        console.error('Mensaje realtime inválido:', error);
      }
    });

    socket.addEventListener('close', function () {
      window.dispatchEvent(new CustomEvent('smartdrop:realtime-status', {
        detail: { connected: false },
      }));
      clearTimeout(retryTimer);
      retryTimer = window.setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 30000);
    });
  }

  connect();
}());
