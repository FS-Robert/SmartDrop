/* Gotita, la asistente de SmartDrop en la página web.
 *
 * - Botón en la esquina y chat; la conversación sigue aunque cambies de página.
 * - Voz: te escucha con el micrófono y te contesta hablando, frase por frase.
 * - "Oye Gotita": con el interruptor encendido te escucha mientras la página está abierta.
 * - Explica la pantalla: lee lo que se ve y, si se lo pides, le toma una foto a la pantalla.
 * - Avisos: cada poco revisa si hay avisos de sensores o fugas y Gotita te los cuenta.
 *
 * Gotita solo responde y te lleva a otras pantallas permitidas; también puede cambiar el modo, el idioma
 * y la velocidad de su voz. Nada más.
 */
(function () {
  'use strict';

  const script = document.currentScript;
  const raiz = document.getElementById('gotita');
  if (!script || !raiz) return;

  const URL_TURNO = script.dataset.turnoUrl;
  const URL_AVISOS = script.dataset.avisosUrl;
  const URL_VOZ = script.dataset.vozUrl;
  const URL_HISTORIAL = script.dataset.historialUrl;
  const URL_DICTADO = script.dataset.dictadoUrl;
  const CSRF = script.dataset.csrf;
  const USUARIO = script.dataset.usuario || 'x';
  const IDIOMA = document.documentElement.lang === 'en' ? 'en' : 'es';
  // Herramienta para tomarle foto a la pantalla (se carga solo cuando se necesita).
  const HTML2CANVAS = 'https://cdn.jsdelivr.net/npm/html2canvas-pro@1.5.8/dist/html2canvas-pro.min.js';
  const REVISION_AVISOS_MS = 90000;
  const MAX_CHAT = 60;
  const MAX_HISTORIAL = 6;
  const MAX_PANTALLA = 12000;
  const MAX_SECCIONES = 40;
  // Si el chat queda abierto sin usarse 2 minutos, se esconde (la conversación sigue igual).
  const INACTIVIDAD_MS = 120000;

  let DESTINOS = {};
  try {
    DESTINOS = JSON.parse((document.getElementById('gotita-destinos') || {}).textContent || '{}') || {};
  } catch (e) { DESTINOS = {}; }

  const TEXTOS = {
    es: {
      reposo: 'Lista para ayudarte',
      vigilando: 'Di “Oye Gotita” cuando me necesites',
      escuchando: 'Te escucho…',
      pensando: 'Pensando…',
      hablando: 'Hablando…',
      bienvenida: 'Hola, soy Gotita. Puedo explicarte lo que ves, contarte cómo está tu agua y llevarte a otra pantalla. Escríbeme, toca el micrófono o di “Oye Gotita”.',
      bienvenidaAdmin: 'Hola, soy Gotita. Puedo resumirte alertas, fugas y reportes, explicarte cualquier pantalla y llevarte a donde necesites.',
      sinMicrofono: 'Este navegador no permite dictar por voz. Puedes escribirme, o usar Chrome o Edge para hablarme.',
      sinDictadoNavegador: 'Opera, Brave y Vivaldi no traen dictado por voz. Puedes escribirme, o abrir SmartDrop en Chrome o Edge para hablarme.',
      sinHttps: 'El navegador bloquea el micrófono porque la página no está abierta con https ni en localhost. Ábrela con http://localhost:8000 en esta computadora (o con https), o escríbeme.',
      dictadoBloqueado: 'El navegador no dejó usar su dictado por voz. Revisa en el candado de la barra de direcciones que el micrófono esté permitido y que tengas internet, y vuelve a intentarlo.',
      micBloqueado: 'No tengo permiso para usar el micrófono. Actívalo en el candado de la barra de direcciones.',
      sinConexion: 'No me pude conectar. Revisa tu internet e inténtalo de nuevo.',
      error: 'Tuve un problema para responder. Inténtalo de nuevo.',
      tocaParaEscuchar: 'Toca la pantalla una vez para que pueda hablarte en voz alta (el navegador lo pide).',
      explicarPantalla: 'Explícame esta pantalla',
      modoTecnico: 'Listo, te explico en modo técnico.',
      modoSencillo: 'Listo, te explico en modo sencillo.',
      lenta: 'Listo, hablaré más despacio.',
      normal: 'Listo, vuelvo a la velocidad normal.',
      teEscucho: 'Te escucho.',
      ver: 'Ver',
      abrirChat: 'Abrir chat',
      escuchar: 'Escuchar',
      borrado: 'Listo, empezamos de nuevo.',
      escribe: 'Escribe tu pregunta…',
      idiomaCambiado: 'Listo, ahora te hablo en español.',
      noTeEscuche: 'No te escuché. Revisa que el micrófono esté conectado y que no esté en silencio, y vuelve a intentarlo.',
      micNoAbre: 'No pude abrir el micrófono. Revisa en Windows (Configuración › Privacidad › Micrófono) que el navegador tenga permiso, y que el micrófono correcto esté elegido en el candado de la barra de direcciones.',
      micNoHay: 'No encuentro ningún micrófono en esta computadora. Conecta uno o revisa que esté activado.',
      micSinInternet: 'El dictado por voz necesita internet y no logré conectarme. Revisa tu conexión.',
      sinVozIdioma: 'Este navegador no tiene una voz en español, por eso puede sonar con acento raro. Para una voz suave en español usa Microsoft Edge o Google Chrome.',
      sinVozOpcion: 'No hay voces de este idioma',
      vozNatural: 'Voz natural de Gotita (ElevenLabs)',
      pruebaVoz: 'Hola, soy Gotita. Así sueno con esta voz.',
      escuchaEncendida: 'Listo: mientras esta página esté abierta te escucho. Di “Oye Gotita” y tu pregunta, por ejemplo: “Oye Gotita, ¿cómo está mi tanque?”.',
      escuchaApagada: 'Listo, ya no escucho. Puedes hablarme con el botón del micrófono o escribirme.',
      entendiendo: 'Entendiendo…',
      historial: 'Conversaciones',
      verHistorial: 'Conversaciones anteriores',
      cerrarHistorial: 'Cerrar historial',
      nueva: 'Nueva conversación',
      sinHistorial: 'Aún no tienes conversaciones guardadas.',
      notaHistorial: 'Se borran solas a los {dias} días.',
      borrarTodo: 'Borrar todo el historial',
      confirmarBorrarTodo: '¿Borrar todas tus conversaciones con Gotita? No se pueden recuperar.',
      borrarUna: 'Borrar conversación',
      borrarEsta: 'Borrar esta conversación',
      ayer: 'Ayer',
      noSePudoAbrir: 'No pude abrir esa conversación. Inténtalo de nuevo.',
      historialError: 'No pude cargar el historial.',
      nuevaLista: 'Listo, empezamos una conversación nueva.',
    },
    en: {
      reposo: 'Ready to help',
      vigilando: 'Say “Hey Gotita” whenever you need me',
      escuchando: "I'm listening…",
      pensando: 'Thinking…',
      hablando: 'Speaking…',
      bienvenida: "Hi, I'm Gotita. I can explain what you see, tell you how your water is doing and take you to another screen. Type, tap the microphone or say “Hey Gotita”.",
      bienvenidaAdmin: "Hi, I'm Gotita. I can summarize alerts, leaks and reports, explain any screen and take you wherever you need.",
      sinMicrofono: "This browser doesn't support voice dictation. You can type, or use Chrome or Edge to talk to me.",
      sinDictadoNavegador: "Opera, Brave and Vivaldi don't include voice dictation. You can type, or open SmartDrop in Chrome or Edge to talk to me.",
      sinHttps: 'The browser blocks the microphone because the page is not opened with https or on localhost. Open it with http://localhost:8000 on this computer (or with https), or type to me.',
      dictadoBloqueado: "The browser didn't allow its voice dictation. Check in the lock icon of the address bar that the microphone is allowed and that you have internet, then try again.",
      micBloqueado: "I don't have permission to use the microphone. Allow it from the lock icon in the address bar.",
      sinConexion: "I couldn't connect. Check your internet and try again.",
      error: 'I had a problem answering. Please try again.',
      tocaParaEscuchar: 'Tap the page once so I can speak out loud (the browser requires it).',
      explicarPantalla: 'Explain this screen',
      modoTecnico: "Done, I'll explain things in technical mode.",
      modoSencillo: "Done, I'll explain things in simple mode.",
      lenta: "Done, I'll speak more slowly.",
      normal: 'Done, back to normal speed.',
      teEscucho: "I'm listening.",
      ver: 'View',
      abrirChat: 'Open chat',
      escuchar: 'Listen',
      borrado: "Done, let's start over.",
      escribe: 'Type your question…',
      idiomaCambiado: "Done, I'll speak English now.",
      noTeEscuche: "I didn't hear you. Check that the microphone is connected and not muted, and try again.",
      micNoAbre: "I couldn't open the microphone. In Windows (Settings › Privacy › Microphone) make sure the browser has permission, and pick the right microphone from the lock icon in the address bar.",
      micNoHay: "I can't find a microphone on this computer. Plug one in or make sure it's turned on.",
      micSinInternet: "Voice dictation needs internet and I couldn't connect. Check your connection.",
      sinVozIdioma: "This browser has no English voice, so it may sound odd. For a soft English voice use Microsoft Edge or Google Chrome.",
      sinVozOpcion: 'No voices for this language',
      vozNatural: "Gotita's natural voice (ElevenLabs)",
      pruebaVoz: "Hi, I'm Gotita. This is how I sound with this voice.",
      escuchaEncendida: 'Done: while this page is open I am listening. Say “Hey Gotita” and your question, for example: “Hey Gotita, how is my tank?”.',
      escuchaApagada: "Done, I'm no longer listening. You can talk to me with the microphone button or type.",
      entendiendo: 'Understanding…',
      historial: 'Conversations',
      verHistorial: 'Previous conversations',
      cerrarHistorial: 'Close history',
      nueva: 'New conversation',
      sinHistorial: "You don't have saved conversations yet.",
      notaHistorial: 'They are deleted automatically after {dias} days.',
      borrarTodo: 'Delete all history',
      confirmarBorrarTodo: 'Delete all your conversations with Gotita? They cannot be recovered.',
      borrarUna: 'Delete conversation',
      borrarEsta: 'Delete this conversation',
      ayer: 'Yesterday',
      noSePudoAbrir: "I couldn't open that conversation. Please try again.",
      historialError: "I couldn't load the history.",
      nuevaLista: "Done, let's start a new conversation.",
    },
  };
  // Los textos y sugerencias van en el idioma en que habla Gotita (botón ES/EN o "háblame en inglés").
  let T = TEXTOS[IDIOMA];

  const SUGERENCIAS_POR_IDIOMA = {
    es: {
      inicio: ['¿Cómo está mi agua hoy?', '¿Tengo alguna alerta?'],
      estado_sistema: ['¿Todos los sensores funcionan?'],
      tanque: ['¿Cuánta agua me queda?', '¿Qué significa este porcentaje?'],
      presion: ['¿La presión está bien?', '¿Qué es la presión del agua?'],
      calidad: ['¿El TDS está dentro del rango?', '¿Qué es el TDS?'],
      consumo: ['¿Cuánto gasté hoy?', '¿Cómo puedo ahorrar agua?'],
      retroalimentacion: ['¿Gasto más que otros hogares?'],
      recomendaciones: ['Dame un consejo para ahorrar'],
      alertas: ['Resúmeme mis alertas'],
      comunidad: ['¿Qué problemas hay en mi zona?'],
      panel: ['Resumen de alertas y reportes de hoy', '¿Hay posibles fugas?'],
      valvulas: ['¿Qué muestra esta pantalla?'],
      prediccion_fugas: ['¿Qué viviendas tienen posible fuga?'],
      prediccion_suministro: ['¿Qué zona tiene más riesgo?'],
      reportes_admin: ['¿Cuántos reportes hay pendientes?'],
      otro: ['¿Qué puedo hacer aquí?'],
    },
    en: {
      inicio: ['How is my water today?', 'Do I have any alerts?'],
      estado_sistema: ['Are all sensors working?'],
      tanque: ['How much water is left?', 'What does this percentage mean?'],
      presion: ['Is the pressure OK?', 'What is water pressure?'],
      calidad: ['Is TDS within range?', 'What is TDS?'],
      consumo: ['How much did I use today?', 'How can I save water?'],
      retroalimentacion: ['Do I use more than other homes?'],
      recomendaciones: ['Give me a water-saving tip'],
      alertas: ['Summarize my alerts'],
      comunidad: ['What problems are there in my area?'],
      panel: ["Summary of today's alerts and reports", 'Are there possible leaks?'],
      valvulas: ['What does this screen show?'],
      prediccion_fugas: ['Which homes may have a leak?'],
      prediccion_suministro: ['Which zone has the highest risk?'],
      reportes_admin: ['How many reports are pending?'],
      otro: ['What can I do here?'],
    },
  };
  let SUGERENCIAS = SUGERENCIAS_POR_IDIOMA[IDIOMA];

  // ── Lo que Gotita recuerda en este navegador (si el navegador no deja guardar, sigue igual) ──
  function leer(almacen, clave, defecto) {
    try {
      const valor = almacen.getItem('gotita:' + clave);
      return valor === null ? defecto : JSON.parse(valor);
    } catch (e) { return defecto; }
  }
  function guardar(almacen, clave, valor) {
    try { almacen.setItem('gotita:' + clave, JSON.stringify(valor)); } catch (e) { /* sin almacenamiento */ }
  }
  const local = window.localStorage;
  const sesion = window.sessionStorage;
  // Lo guardado en este navegador es de quien inició sesión. Si entra otra cuenta (una computadora compartida),
  // se borran la conversación y los ajustes de la anterior, incluida la escucha con "Oye Gotita".
  [local, sesion].forEach((almacen) => {
    if (leer(almacen, 'usuario', null) === USUARIO) return;
    try {
      Object.keys(almacen).filter((clave) => clave.startsWith('gotita:')).forEach((clave) => almacen.removeItem(clave));
    } catch (e) { /* sin almacenamiento */ }
    guardar(almacen, 'usuario', USUARIO);
  });

  const ajustes = {
    modo: leer(local, 'modo', 'sencillo'),
    velocidad: leer(local, 'velocidad', 'normal'),
    escucha: leer(local, 'escucha', false),
    voz: leer(local, 'voz', true),
    leerAvisos: leer(local, 'leer-avisos', true),
    idioma: leer(local, 'idioma', IDIOMA),
    voces: leer(local, 'voces', {}), // la voz que eligió la persona para cada idioma
  };
  if (!ajustes.voces || typeof ajustes.voces !== 'object') ajustes.voces = {};
  if (ajustes.idioma !== 'es' && ajustes.idioma !== 'en') ajustes.idioma = IDIOMA;
  T = TEXTOS[ajustes.idioma];
  SUGERENCIAS = SUGERENCIAS_POR_IDIOMA[ajustes.idioma];
  let chat = leer(sesion, 'chat', []);
  if (!Array.isArray(chat)) chat = [];
  // Número de la conversación en el servidor. Vive en esta pestaña: si la cierras, la próxima vez es una nueva.
  let conversacionId = leer(sesion, 'conversacion', null);
  if (typeof conversacionId !== 'number') conversacionId = null;
  let ultimaActividad = Number(leer(sesion, 'actividad', 0)) || 0;

  // ── Partes de la pantalla de Gotita ──
  const $ = (id) => document.getElementById(id);
  const el = {
    boton: $('gotita-boton'), punto: $('gotita-punto'), panel: $('gotita-panel'), estado: $('gotita-estado'),
    cerrar: $('gotita-cerrar'), ajustesBoton: $('gotita-ajustes-boton'), ajustes: $('gotita-ajustes'),
    escucha: $('gotita-escucha'), voz: $('gotita-voz'), leerAvisos: $('gotita-leer-avisos'), borrar: $('gotita-borrar'),
    vozLista: $('gotita-voz-lista'), vozProbar: $('gotita-voz-probar'),
    chat: $('gotita-chat'), sugerencias: $('gotita-sugerencias'), form: $('gotita-form'), texto: $('gotita-texto'),
    mic: $('gotita-mic'), parar: $('gotita-parar'), burbuja: $('gotita-burbuja'), burbujaTexto: $('gotita-burbuja-texto'),
    burbujaVer: $('gotita-burbuja-ver'), burbujaEscuchar: $('gotita-burbuja-escuchar'),
    burbujaAbrir: $('gotita-burbuja-abrir'), burbujaCerrar: $('gotita-burbuja-cerrar'),
    historialBoton: $('gotita-historial-boton'), historial: $('gotita-historial'),
    historialCerrar: $('gotita-historial-cerrar'), historialLista: $('gotita-historial-lista'),
    historialTitulo: $('gotita-historial-titulo'), historialNota: $('gotita-historial-nota'),
    historialTodo: $('gotita-historial-todo'), nueva: $('gotita-nueva'),
  };

  // ── Qué está haciendo Gotita (escuchando, pensando, hablando…) ──
  let estado = 'reposo';
  function estadoBase() {
    return ajustes.escucha && oido.disponible ? 'vigilando' : 'reposo';
  }
  function ponerEstado(nuevo) {
    estado = nuevo;
    raiz.dataset.estado = nuevo;
    el.estado.textContent = T[nuevo] || T.reposo;
    el.parar.hidden = !(nuevo === 'hablando' || nuevo === 'pensando');
    el.mic.setAttribute('aria-pressed', nuevo === 'escuchando' ? 'true' : 'false');
  }

  function abierto() { return !el.panel.hidden; }

  function abrir(enfocar) {
    marcarActividad();
    el.panel.hidden = false;
    raiz.classList.add('is-abierta');
    el.boton.setAttribute('aria-expanded', 'true');
    ocultarBurbuja();
    el.punto.hidden = true;
    guardar(sesion, 'abierto', true);
    pintarChat();
    pintarSugerencias();
    if (enfocar !== false) el.texto.focus({ preventScroll: true });
  }

  function cerrar(sinEnfocar) {
    el.panel.hidden = true;
    raiz.classList.remove('is-abierta');
    el.boton.setAttribute('aria-expanded', 'false');
    guardar(sesion, 'abierto', false);
    if (!sinEnfocar) el.boton.focus({ preventScroll: true });
  }

  // ── Se esconde solo si nadie lo usa en 2 minutos ──
  function marcarActividad() {
    ultimaActividad = Date.now();
    guardar(sesion, 'actividad', ultimaActividad);
  }
  function revisarInactividad() {
    if (!abierto() || estado === 'pensando' || estado === 'escuchando' || voz.hablando) return;
    if (el.panel.contains(document.activeElement) && el.texto.value.trim()) return; // está escribiendo
    if (Date.now() - ultimaActividad >= INACTIVIDAD_MS) cerrar(true);
  }

  // ── Conversación ──
  function guardarChat() {
    chat = chat.slice(-MAX_CHAT);
    guardar(sesion, 'chat', chat);
  }

  function nodoMensaje(mensaje) {
    const fila = document.createElement('div');
    fila.className = 'gotita-msj gotita-msj--' + mensaje.rol;
    if (mensaje.rol !== 'usuario') {
      const avatar = document.createElement('span');
      avatar.className = 'gotita-avatar';
      avatar.setAttribute('aria-hidden', 'true');
      avatar.innerHTML = '<svg viewBox="0 0 64 64"><use href="#gotita-simbolo"></use></svg>';
      fila.appendChild(avatar);
    }
    const contenido = document.createElement('div');
    const parrafo = document.createElement('p');
    parrafo.textContent = mensaje.texto;
    contenido.appendChild(parrafo);
    if (mensaje.url) {
      const enlace = document.createElement('a');
      enlace.className = 'gotita-enlace gotita-msj-enlace';
      enlace.href = mensaje.url;
      enlace.textContent = T.ver;
      contenido.appendChild(enlace);
    }
    fila.appendChild(contenido);
    return fila;
  }

  function pintarChat() {
    el.chat.textContent = '';
    if (!chat.length) {
      const hola = document.createElement('p');
      hola.className = 'gotita-bienvenida';
      hola.textContent = script.dataset.rol === 'admin' ? T.bienvenidaAdmin : T.bienvenida;
      el.chat.appendChild(hola);
    }
    chat.forEach((mensaje) => el.chat.appendChild(nodoMensaje(mensaje)));
    bajarChat();
  }

  function bajarChat() {
    el.chat.scrollTop = el.chat.scrollHeight;
  }

  function agregar(rol, texto, extra) {
    marcarActividad();
    const mensaje = Object.assign({ rol: rol, texto: texto }, extra || {});
    chat.push(mensaje);
    guardarChat();
    if (abierto()) {
      const bienvenida = el.chat.querySelector('.gotita-bienvenida');
      if (bienvenida) bienvenida.remove();
      el.chat.appendChild(nodoMensaje(mensaje));
      bajarChat();
    }
    return mensaje;
  }

  let nodoEscribiendo = null;
  function mostrarEscribiendo(si) {
    if (nodoEscribiendo) { nodoEscribiendo.remove(); nodoEscribiendo = null; }
    if (!si || !abierto()) return;
    nodoEscribiendo = document.createElement('div');
    nodoEscribiendo.className = 'gotita-msj gotita-msj--gotita';
    nodoEscribiendo.innerHTML = '<span class="gotita-avatar" aria-hidden="true"><svg viewBox="0 0 64 64"><use href="#gotita-simbolo"></use></svg></span>' +
      '<span class="gotita-escribiendo" aria-hidden="true"><span></span><span></span><span></span></span>';
    el.chat.appendChild(nodoEscribiendo);
    bajarChat();
  }

  let nodoInterino = null;
  function mostrarInterino(texto) {
    if (!abierto()) { mostrarBurbuja(texto + '…', {}); return; }
    if (!nodoInterino) {
      nodoInterino = nodoMensaje({ rol: 'usuario', texto: '' });
      nodoInterino.classList.add('gotita-msj--interino');
      el.chat.appendChild(nodoInterino);
    }
    nodoInterino.querySelector('p').textContent = texto;
    bajarChat();
  }
  function quitarInterino() {
    if (nodoInterino) { nodoInterino.remove(); nodoInterino = null; }
  }

  // ── Pantalla actual ──
  function pantallaId() {
    const ruta = window.location.pathname;
    for (const [id, url] of Object.entries(DESTINOS)) {
      if (url === ruta) return id;
    }
    const limpio = ruta.replace(/^\/+|\/+$/g, '').replace(/[^a-z0-9]+/gi, '_').toLowerCase();
    return (limpio || 'inicio').slice(0, 40);
  }

  function zonaPrincipal() {
    return document.getElementById('contenido') || document.querySelector('main') || document.body;
  }

  // Pantallas con datos personales o de otras personas (perfil, NIC y titular, paneles del admin, fugas con
  // nombres y direcciones, buscador, detalle de un reporte): su texto y su foto nunca se le mandan a Claude.
  const PANTALLAS_PRIVADAS = new Set(['perfil', 'vincular_vivienda', 'panel', 'valvulas', 'prediccion_fugas',
    'reportes_admin', 'buscar', 'consumo_admin']);
  function pantallaPrivada() {
    return PANTALLAS_PRIVADAS.has(pantallaId()) || /^\/(reportes|admin-reportes|admin-consumo|sensor|usuario)\//.test(window.location.pathname);
  }

  function textoVisible() {
    // Todo el texto de la página, también lo que está más abajo (sin el chat de Gotita). Lo que la persona
    // escribe en los campos no se manda (el innerText no lo trae).
    const principal = zonaPrincipal();
    let texto = principal.innerText || '';
    if (principal.contains(raiz)) texto = texto.replace(raiz.innerText || '', '');
    return texto.replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n').trim().slice(0, MAX_PANTALLA);
  }

  // Partes de la pantalla (cada título) a las que Gotita puede bajar la vista con "muéstrame…".
  let seccionesPagina = {};
  function recogerSecciones() {
    const principal = zonaPrincipal();
    const lista = [];
    const usados = new Set();
    seccionesPagina = {};
    principal.querySelectorAll('h1, h2, h3, h4, [data-gotita-seccion]').forEach((titulo) => {
      if (lista.length >= MAX_SECCIONES || raiz.contains(titulo) || !titulo.getClientRects().length) return;
      const texto = (titulo.dataset.gotitaSeccion || titulo.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 80);
      if (!texto) return;
      const base = texto.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
        .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 34) || 'parte';
      let id = base;
      for (let n = 2; usados.has(id); n += 1) id = base + '_' + n;
      usados.add(id);
      // Se resalta la tarjeta completa del título, si es una tarjeta pequeña.
      let bloque = titulo.closest('section, article, .card, [class*="card"], [class*="tarjeta"], [class*="panel"]');
      if (!bloque || bloque === principal || !principal.contains(bloque) ||
          bloque.querySelectorAll('h1, h2, h3, h4').length > 3) bloque = titulo;
      seccionesPagina[id] = bloque;
      lista.push({ id: id, titulo: texto });
    });
    return lista;
  }

  function mostrarSeccion(id) {
    const destino = seccionesPagina[id];
    if (!destino || !document.contains(destino)) return;
    // En el teléfono el chat tapa la página: se esconde para que se vea la parte (la conversación sigue).
    if (abierto() && window.matchMedia('(max-width: 599.98px)').matches) cerrar(true);
    const suave = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    destino.scrollIntoView({ behavior: suave ? 'smooth' : 'auto', block: 'center' });
    destino.classList.add('gotita-resaltado');
    window.setTimeout(() => destino.classList.remove('gotita-resaltado'), 3500);
  }

  // El texto de la pantalla solo va cuando la pregunta es sobre lo que se ve (y nunca en pantallas privadas);
  // para lo demás Gotita usa los datos de la persona que le da el servidor.
  function leerPantalla(conTexto) {
    return {
      id: pantallaId(),
      titulo: (document.title || '').replace(/^SmartDrop\s*[–-]\s*/, '').slice(0, 80),
      texto: conTexto && !pantallaPrivada() ? textoVisible() : '',
      secciones: recogerSecciones(),
    };
  }

  let cargaHtml2canvas = null;
  function cargarHtml2canvas() {
    if (window.html2canvas) return Promise.resolve(window.html2canvas);
    if (!cargaHtml2canvas) {
      cargaHtml2canvas = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = HTML2CANVAS;
        // Si el CDN entregara otro archivo, el navegador no lo ejecuta (integridad del archivo publicado en npm).
        s.integrity = 'sha384-QbVSYhU9faw2C7l92rI0Dmke8Yod6KaOixC1kkbO/dGnMDKtbWhwcxSSOkmHXWom';
        s.crossOrigin = 'anonymous';
        s.referrerPolicy = 'no-referrer';
        s.onload = () => resolve(window.html2canvas);
        s.onerror = () => { cargaHtml2canvas = null; reject(new Error('html2canvas')); };
        document.head.appendChild(s);
      });
    }
    return cargaHtml2canvas;
  }

  /* Foto de lo que se ve en la pantalla (sin Gotita), pequeña para que viaje rápido. */
  async function capturarPantalla() {
    try {
      const html2canvas = await cargarHtml2canvas();
      const escala = Math.min(1, 1280 / Math.max(window.innerWidth, 1));
      const fondo = getComputedStyle(document.body).backgroundColor || '#ffffff';
      const lienzo = await html2canvas(document.body, {
        x: window.scrollX, y: window.scrollY, width: window.innerWidth, height: window.innerHeight,
        windowWidth: document.documentElement.clientWidth, scale: escala, backgroundColor: fondo,
        logging: false, useCORS: true,
        ignoreElements: (nodo) => nodo.id === 'gotita' || nodo.classList?.contains('gotita-simbolos'),
        // Se quitan las animaciones para que en la foto no salga todo en blanco.
        onclone: (copia) => {
          const estilo = copia.createElement('style');
          estilo.textContent = '*,*::before,*::after{animation:none!important;transition:none!important}';
          copia.head.appendChild(estilo);
        },
      });
      for (const calidad of [0.72, 0.55, 0.4]) {
        const datos = lienzo.toDataURL('image/jpeg', calidad);
        if (datos.length < 1400000) return datos;
      }
    } catch (e) { /* sin captura: Gotita usa el texto de la pantalla */ }
    return null;
  }

  const PIDE_PANTALLA = /(esta pantalla|lo que (veo|estoy viendo|sale|aparece)|qu[eé] (estoy viendo|veo|es esto|significa esto)|esta gr[aá]fica|este gr[aá]fico|explica(me)? (esto|la pantalla)|this screen|what am i (seeing|looking at)|this chart)/i;

  // ── Voz de Gotita ──
  const MAX_TROZO = 220; // letras por tanda: así no hay pausas entre frases y el navegador no la corta
  const voz = {
    // Voz natural de ElevenLabs (desde el servidor); si no está o falla, la voz del navegador.
    natural: script.dataset.vozNatural === '1' && !!URL_VOZ,
    navegador: 'speechSynthesis' in window,
    disponible: ('speechSynthesis' in window) || (script.dataset.vozNatural === '1' && !!URL_VOZ),
    cola: [],
    colaNatural: [],
    audio: null,
    control: null,
    hablando: false,
    bloqueada: false,
    pendientes: [],
    elegida: null,
    esperas: [],
    avisoSinVoz: false,

    /* Voces de la computadora en el idioma de Gotita, de la más suave y confiable a la menos. */
    delIdioma() {
      if (!this.navegador) return [];
      const ingles = ajustes.idioma === 'en';
      const esEdge = /Edg\//.test(navigator.userAgent);
      const femeninas = ingles
        ? /(Aria|Jenny|Ava|Emma|Michelle|Zira|Samantha|female)/i
        : /(Dalia|Paloma|Elvira|Salome|Ximena|Larissa|Elena|Camila|Andrea|Sabina|Helena|Laura|Paulina|M[oó]nica|female)/i;
      const puntaje = (v) => {
        let p = 0;
        if (/natural/i.test(v.name)) p += 50; // las voces naturales de Edge son las más suaves
        if (esEdge && /online/i.test(v.name)) p += 10;
        if (v.localService) p += 20; // las que vienen en la computadora no fallan por internet
        if (femeninas.test(v.name)) p += 8;
        if ((ingles ? /en[-_]US/i : /es[-_](US|MX|419|SV)/i).test(v.lang)) p += 5;
        if (/google/i.test(v.name)) p += 3;
        return p;
      };
      return window.speechSynthesis.getVoices()
        .filter((v) => (v.lang || '').toLowerCase().startsWith(ingles ? 'en' : 'es'))
        .sort((a, b) => puntaje(b) - puntaje(a));
    },

    /* La voz guardada en los ajustes o, si no hay, la mejor del idioma. */
    elegir() {
      const lista = this.delIdioma();
      const guardada = (ajustes.voces || {})[ajustes.idioma];
      this.elegida = lista.find((v) => v.name === guardada) || lista[0] || null;
      pintarVoces(lista);
    },

    decir(texto) {
      if (!this.disponible || !ajustes.voz || !texto) return;
      if (this.bloqueada) { this.pendientes.push(texto); return; }
      if (this.natural) {
        // El audio de cada frase se pide de una vez, mientras suena la anterior: así no hay pausas.
        this.colaNatural.push({ texto: texto, audio: this.pedirAudio(texto) });
        this.siguienteNatural();
        return;
      }
      if (!this.navegador) return;
      this.cola.push(texto);
      this.siguiente();
    },

    /* Le pide al servidor el audio de la frase con la voz natural. */
    pedirAudio(texto) {
      if (!this.control) this.control = new AbortController();
      const senal = this.control.signal;
      return fetch(URL_VOZ, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'audio/mpeg', 'X-CSRFToken': CSRF },
        body: JSON.stringify({ texto: texto, idioma: ajustes.idioma, lenta: ajustes.velocidad === 'lenta' }),
        signal: senal,
      })
        .then((respuesta) => (respuesta.ok ? respuesta.blob() : Promise.reject(respuesta.status)))
        .then((audio) => URL.createObjectURL(audio))
        .catch(() => (senal.aborted ? 'cancelado' : null));
    },

    async siguienteNatural() {
      if (this.hablando) return;
      const item = this.colaNatural.shift();
      if (!item) {
        if (estado === 'hablando') ponerEstado(estadoBase());
        this.esperas.splice(0).forEach((fin) => fin());
        return;
      }
      this.hablando = true;
      ponerEstado('hablando');
      const direccion = await item.audio;
      if (direccion === 'cancelado') { this.hablando = false; return; }
      if (!direccion) {
        // La voz natural no respondió: Gotita sigue con la voz del navegador para no quedarse muda.
        this.hablando = false;
        this.usarVozDelNavegador([item.texto].concat(this.colaNatural.splice(0).map((i) => i.texto)));
        return;
      }
      const audio = new Audio(direccion);
      this.audio = audio;
      const seguir = () => {
        URL.revokeObjectURL(direccion);
        if (this.audio !== audio) return;
        this.audio = null;
        this.hablando = false;
        this.siguienteNatural();
      };
      audio.onended = seguir;
      audio.onerror = seguir;
      try {
        await audio.play();
      } catch (error) {
        URL.revokeObjectURL(direccion);
        this.audio = null;
        this.hablando = false;
        if (error && error.name === 'NotAllowedError') {
          // El navegador no deja sonar audio hasta que la persona toque la página una vez.
          this.bloqueada = true;
          this.pendientes.push(item.texto, ...this.colaNatural.splice(0).map((i) => i.texto));
          avisarDesbloqueo();
          this.esperas.splice(0).forEach((fin) => fin());
          if (estado === 'hablando') ponerEstado(estadoBase());
          return;
        }
        this.siguienteNatural();
      }
    },

    usarVozDelNavegador(textos) {
      this.natural = false;
      this.elegir();
      if (!this.navegador) return;
      textos.forEach((t) => this.cola.push(t));
      this.siguiente();
    },

    siguiente() {
      if (this.hablando) return;
      // Junta las frases que ya llegaron para leerlas de corrido, sin pausas entre una y otra.
      let texto = this.cola.shift();
      while (texto && this.cola.length && (texto + ' ' + this.cola[0]).length <= MAX_TROZO) {
        texto += ' ' + this.cola.shift();
      }
      if (!texto) {
        if (estado === 'hablando') ponerEstado(estadoBase());
        this.esperas.splice(0).forEach((fin) => fin());
        return;
      }
      if (!this.elegida) this.elegir();
      if (!this.elegida && window.speechSynthesis.getVoices().length && !this.avisoSinVoz) {
        // Sin voz del idioma, el navegador leería con acento de otro idioma: se avisa una vez.
        this.avisoSinVoz = true;
        agregar('error', T.sinVozIdioma);
      }
      const frase = new SpeechSynthesisUtterance(texto);
      if (this.elegida) frase.voice = this.elegida;
      frase.lang = this.elegida ? this.elegida.lang : (ajustes.idioma === 'en' ? 'en-US' : 'es-US');
      frase.rate = ajustes.velocidad === 'lenta' ? 0.85 : 1;
      frase.pitch = 1;
      frase.volume = 1;
      // Por si el navegador no avisa que terminó de hablar, se sigue sola después de un rato.
      const vigilante = window.setTimeout(() => {
        if (this.hablando) { this.hablando = false; this.siguiente(); }
      }, 4000 + texto.length * (ajustes.velocidad === 'lenta' ? 100 : 85));
      frase.onend = () => { window.clearTimeout(vigilante); this.hablando = false; this.siguiente(); };
      frase.onerror = (evento) => {
        window.clearTimeout(vigilante);
        this.hablando = false;
        if (evento.error === 'not-allowed') {
          // El navegador no deja hablar hasta que la persona toque la página una vez.
          this.bloqueada = true;
          this.pendientes.push(texto, ...this.cola.splice(0));
          avisarDesbloqueo();
          this.esperas.splice(0).forEach((fin) => fin());
          if (estado === 'hablando') ponerEstado(estadoBase());
          return;
        }
        this.siguiente();
      };
      this.hablando = true;
      ponerEstado('hablando');
      window.speechSynthesis.speak(frase);
    },

    parar() {
      this.cola = [];
      this.colaNatural = [];
      this.pendientes = [];
      if (this.control) { this.control.abort(); this.control = null; }
      if (this.audio) { this.audio.pause(); this.audio = null; }
      if (this.navegador) window.speechSynthesis.cancel();
      this.hablando = false;
      this.esperas.splice(0).forEach((fin) => fin());
      if (estado === 'hablando') ponerEstado(estadoBase());
    },

    desbloquear() {
      if (!this.bloqueada) return;
      this.bloqueada = false;
      const pendientes = this.pendientes.splice(0);
      pendientes.forEach((t) => this.decir(t));
    },

    /* Espera a que Gotita termine de hablar (o un tiempo máximo). */
    terminar(maximo) {
      if (!this.hablando && !this.cola.length && !this.colaNatural.length) return Promise.resolve();
      return new Promise((resolve) => {
        this.esperas.push(resolve);
        window.setTimeout(resolve, maximo);
      });
    },
  };

  /* Lista de voces en los ajustes, para que cada quien elija la que le guste. */
  function pintarVoces(lista) {
    if (!el.vozLista) return;
    el.vozLista.textContent = '';
    if (voz.natural) {
      // Con la voz natural (ElevenLabs) no hace falta elegir voces del navegador.
      const natural = document.createElement('option');
      natural.textContent = T.vozNatural;
      el.vozLista.appendChild(natural);
      el.vozLista.disabled = true;
      return;
    }
    if (!lista.length) {
      const nada = document.createElement('option');
      nada.textContent = T.sinVozOpcion;
      el.vozLista.appendChild(nada);
      el.vozLista.disabled = true;
      return;
    }
    el.vozLista.disabled = false;
    lista.forEach((v) => {
      const opcion = document.createElement('option');
      opcion.value = v.name;
      opcion.textContent = v.name.replace(/^Microsoft\s+/i, '').replace(/\s+-\s+.*$/, '') + ' (' + v.lang + ')';
      opcion.selected = voz.elegida && v.name === voz.elegida.name;
      el.vozLista.appendChild(opcion);
    });
  }

  if (voz.navegador) {
    window.speechSynthesis.addEventListener?.('voiceschanged', () => voz.elegir());
  }

  function avisarDesbloqueo() {
    el.burbujaEscuchar.hidden = false;
    mostrarBurbuja(T.tocaParaEscuchar, {});
    const desbloquear = () => {
      document.removeEventListener('pointerdown', desbloquear, true);
      document.removeEventListener('keydown', desbloquear, true);
      el.burbujaEscuchar.hidden = true;
      voz.desbloquear();
    };
    document.addEventListener('pointerdown', desbloquear, true);
    document.addEventListener('keydown', desbloquear, true);
  }

  // ── Oído de Gotita (micrófono) ──
  const Reconocedor = window.SpeechRecognition || window.webkitSpeechRecognition;
  // Opera, Brave y Vivaldi dicen que tienen dictado por voz, pero no funciona: solo Chrome y Edge.
  const NAVEGADOR_SIN_DICTADO = /OPR\/|Opera|Vivaldi/i.test(navigator.userAgent) || !!navigator.brave;
  // Formas en que el micrófono puede entender "Gotita" en español o inglés ("go tita", "godita"…).
  const NOMBRE = '(?:gotita|gotitas|go\\s?tita|got\\s?ita|got it a|godita|gotica|cotita)';
  const DESPERTAR = new RegExp(
    '(?:^|[\\s,.¡!¿?])(?:(?:hey|oye|oiga|ok|okay|okey|hola|ey|ei|hi|mira|mirá|vaya|ve)[\\s,]+)?' + NOMBRE + '\\b[\\s,.:;!?¡¿]*(.*)$', 'i');
  const PARAR = new RegExp(
    '^(?:' + NOMBRE + '[\\s,]*)?(?:para(?:le)?|p[aá]rale|detente|alto|silencio|c[aá]llate|c[aá]llese|basta|ya basta|ya estuvo|ya no|ya|stop|be quiet|shut up)[\\s.!]*$', 'i');
  const LOCAL = '^(?:' + NOMBRE + '[\\s,]*)?';

  // ── Dictado natural: se graba la pregunta y ElevenLabs la pasa a texto en el servidor ──
  // Entiende español e inglés sin tocar el botón ES/EN (el dictado del navegador solo oye un idioma a la vez).
  // Si la llave no tiene permiso o algo falla, se usa el dictado del navegador como antes.
  const dictado = {
    disponible: script.dataset.dictadoNatural === '1' && !!URL_DICTADO && !!window.MediaRecorder &&
      !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) && window.isSecureContext,
    grabando: false,
    terminar: null, // corta la grabación antes (botón del micrófono otra vez)
    cancelado: false,

    tipoDeAudio() {
      const tipos = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
      return tipos.find((t) => window.MediaRecorder.isTypeSupported && window.MediaRecorder.isTypeSupported(t)) || '';
    },

    /* Graba hasta que la persona se calla (o 15 segundos) y devuelve { texto, idioma } o { error } o { otroDictado }. */
    async escuchar() {
      let medios;
      try {
        medios = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      } catch (e) {
        if (e && e.name === 'NotAllowedError') return { error: T.micBloqueado };
        if (e && e.name === 'NotFoundError') return { error: T.micNoHay };
        return { error: T.micNoAbre };
      }
      const tipo = this.tipoDeAudio();
      let grabadora;
      try {
        grabadora = new MediaRecorder(medios, tipo ? { mimeType: tipo } : undefined);
      } catch (e) {
        medios.getTracks().forEach((pista) => pista.stop());
        return { otroDictado: true };
      }
      const partes = [];
      grabadora.ondataavailable = (evento) => { if (evento.data && evento.data.size) partes.push(evento.data); };
      const detenida = new Promise((listo) => { grabadora.onstop = listo; });

      // Se mide el volumen para saber cuándo empezó a hablar y cuándo se calló.
      const Contexto = window.AudioContext || window.webkitAudioContext;
      const ctx = new Contexto();
      const analizador = ctx.createAnalyser();
      analizador.fftSize = 1024;
      ctx.createMediaStreamSource(medios).connect(analizador);
      const muestras = new Float32Array(analizador.fftSize);

      this.grabando = true;
      this.cancelado = false;
      grabadora.start(250);
      const inicio = Date.now();
      let ruido = 0;
      let medidas = 0;
      let hablo = false;
      let ultimaVoz = inicio;
      await new Promise((listo) => {
        this.terminar = listo;
        const revisar = () => {
          if (!this.grabando || grabadora.state !== 'recording') { listo(); return; }
          analizador.getFloatTimeDomainData(muestras);
          let suma = 0;
          for (let i = 0; i < muestras.length; i += 1) suma += muestras[i] * muestras[i];
          const volumen = Math.sqrt(suma / muestras.length);
          const ahora = Date.now();
          if (ahora - inicio < 300) { ruido += volumen; medidas += 1; } // ruido del cuarto, para comparar
          const umbral = Math.max(0.015, (medidas ? ruido / medidas : 0) * 3);
          if (volumen > umbral) { hablo = true; ultimaVoz = ahora; }
          if ((hablo && ahora - ultimaVoz > 1300) || (!hablo && ahora - inicio > 7000) || ahora - inicio > 15000) {
            listo();
            return;
          }
          window.setTimeout(revisar, 100);
        };
        revisar();
      });
      this.terminar = null;
      this.grabando = false;
      if (grabadora.state !== 'inactive') grabadora.stop();
      await detenida;
      medios.getTracks().forEach((pista) => pista.stop());
      ctx.close().catch(() => {});
      if (this.cancelado) return { cancelado: true };
      if (!hablo || !partes.length) return { texto: '' };

      ponerEstado('pensando');
      el.estado.textContent = T.entendiendo;
      const audio = new Blob(partes, { type: grabadora.mimeType || tipo || 'audio/webm' });
      try {
        const respuesta = await fetch(URL_DICTADO, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': audio.type.split(';')[0], 'X-CSRFToken': CSRF, Accept: 'application/json' },
          body: audio,
        });
        const datos = await respuesta.json().catch(() => ({}));
        if (respuesta.ok && datos.ok) return { texto: String(datos.texto || ''), idioma: datos.idioma || null };
        // Sin permiso, sin saldo o límite del día: desde ahora se usa el dictado del navegador.
        if (datos.sin_permiso) this.disponible = false;
        return { otroDictado: true };
      } catch (e) {
        return { error: T.sinConexion };
      }
    },

    parar() {
      this.cancelado = true;
      this.grabando = false;
      if (this.terminar) this.terminar();
    },
  };

  // Por qué no se puede dictar aquí, con el motivo real (no siempre es culpa del navegador).
  function motivoSinDictado() {
    if (NAVEGADOR_SIN_DICTADO) return T.sinDictadoNavegador;
    if (!Reconocedor) return T.sinMicrofono;
    if (!window.isSecureContext) return T.sinHttps;
    return T.dictadoBloqueado;
  }

  const oido = {
    disponible: !!Reconocedor,
    rec: null,
    corriendo: false,
    modo: 'apagado', // apagado · vigilando · comando
    idioma: ajustes.idioma === 'en' ? 'en-US' : 'es-US',
    reintentos: 0,
    fallosDeRed: 0,
    temporizador: null,
    revisarAudio: null,
    oyendo: false,
    textoComando: '',

    crear() {
      const rec = new Reconocedor();
      rec.lang = this.idioma;
      rec.continuous = true;
      rec.interimResults = true;
      rec.maxAlternatives = 1;
      rec.onresult = (evento) => this.resultado(evento);
      rec.onerror = (evento) => this.error(evento);
      rec.onend = () => this.fin();
      rec.onstart = () => { this.reintentos = 0; };
      rec.onaudiostart = () => { this.oyendo = true; };
      rec.onaudioend = () => { this.oyendo = false; };
      return rec;
    },

    /* Si no hay respuesta, explica en el chat por qué el micrófono no funciona. */
    avisar(texto) {
      agregar('error', texto);
      if (!abierto()) mostrarBurbuja(texto, {});
    },

    arrancar() {
      if (!this.disponible || this.corriendo || document.hidden) return;
      try {
        this.rec = this.rec || this.crear();
        this.rec.lang = this.idioma;
        this.rec.start();
        this.corriendo = true;
      } catch (e) {
        this.corriendo = false;
      }
    },

    detener() {
      window.clearTimeout(this.temporizador);
      this.modo = 'apagado';
      if (this.rec && this.corriendo) {
        try { this.rec.abort(); } catch (e) { /* ya estaba detenido */ }
      }
      this.corriendo = false;
    },

    vigilar() {
      this.modo = 'vigilando';
      ponerEstado(voz.hablando ? 'hablando' : 'vigilando');
      this.arrancar();
    },

    escucharComando() {
      if (dictado.disponible) { this.escucharConDictado(); return; }
      if (!this.disponible || NAVEGADOR_SIN_DICTADO || !window.isSecureContext) {
        this.avisar(motivoSinDictado());
        abrir();
        return;
      }
      voz.parar();
      this.modo = 'comando';
      this.textoComando = '';
      ponerEstado('escuchando');
      this.arrancar();
      window.clearTimeout(this.temporizador);
      window.clearTimeout(this.revisarAudio);
      // Si en 4 segundos el micrófono no se abrió, se avisa en vez de quedarse esperando.
      this.revisarAudio = window.setTimeout(() => {
        if (this.modo === 'comando' && !this.oyendo) {
          this.avisar(T.micNoAbre);
          this.terminarComando('');
        }
      }, 4000);
      this.temporizador = window.setTimeout(() => {
        if (!this.textoComando) this.avisar(T.noTeEscuche);
        this.terminarComando();
      }, 10000);
    },

    /* La pregunta se graba y se pasa a texto en el servidor (entiende español e inglés). */
    async escucharConDictado() {
      if (dictado.grabando) return;
      voz.parar();
      // Mientras se graba, el oído de "Oye Gotita" descansa para no oír dos veces.
      this.modo = 'dictando';
      if (this.rec && this.corriendo) { try { this.rec.abort(); } catch (e) { /* nada */ } }
      ponerEstado('escuchando');
      mostrarInterino('…');
      const resultado = await dictado.escuchar();
      quitarInterino();
      this.modo = 'apagado';
      if (resultado.otroDictado) {
        // El dictado natural no se pudo usar: se usa el del navegador para esta y las siguientes preguntas.
        dictado.disponible = false;
        this.escucharComando();
        return;
      }
      if (ajustes.escucha) this.vigilar();
      if (resultado.cancelado) { ponerEstado(estadoBase()); return; }
      if (resultado.error) { this.avisar(resultado.error); ponerEstado(estadoBase()); return; }
      if (!resultado.texto.trim()) {
        this.avisar(T.noTeEscuche);
        ponerEstado(estadoBase());
        return;
      }
      preguntar(resultado.texto, { porVoz: true, idiomaHablado: resultado.idioma });
    },

    terminarComando(texto) {
      window.clearTimeout(this.temporizador);
      window.clearTimeout(this.revisarAudio);
      const pregunta = (texto || this.textoComando || '').trim();
      this.textoComando = '';
      quitarInterino();
      if (ajustes.escucha) {
        this.modo = 'vigilando';
      } else {
        this.modo = 'apagado';
        if (this.rec && this.corriendo) { try { this.rec.stop(); } catch (e) { /* nada */ } }
      }
      if (pregunta) preguntar(pregunta, { porVoz: true });
      else ponerEstado(estadoBase());
    },

    resultado(evento) {
      this.fallosDeRed = 0; // ya llegó algo: la conexión está bien
      for (let i = evento.resultIndex; i < evento.results.length; i += 1) {
        const resultado = evento.results[i];
        const texto = (resultado[0] && resultado[0].transcript || '').trim();
        if (!texto) continue;

        if (this.modo === 'comando') {
          this.textoComando = texto;
          mostrarInterino(texto);
          if (resultado.isFinal) this.terminarComando(texto);
          continue;
        }
        if (this.modo !== 'vigilando') continue;

        const coincide = DESPERTAR.exec(texto);
        if (!coincide) continue;
        const resto = (coincide[1] || '').trim();
        if (voz.hablando || estado === 'pensando') {
          // Mientras Gotita habla solo se atiende "Gotita, para" (su propia voz también llega al micrófono).
          if (resto && PARAR.test(resto)) { voz.parar(); cancelarPregunta(); }
          continue;
        }
        if (!resultado.isFinal) continue;
        if (resto.length > 1) {
          if (PARAR.test(resto)) { voz.parar(); continue; }
          preguntar(resto, { porVoz: true });
        } else {
          sonar();
          if (!abierto()) mostrarBurbuja(T.teEscucho, {});
          this.escucharComando();
        }
      }
    },

    error(evento) {
      const tipo = evento.error;
      // Chrome manda la voz a internet y a veces la conexión falla un momento: se reintenta solo dos veces
      // antes de avisar (al terminar, fin() vuelve a encender el micrófono).
      if (tipo === 'network' && this.fallosDeRed < 2) {
        this.fallosDeRed += 1;
        return;
      }
      const graves = {
        'not-allowed': T.micBloqueado,
        'service-not-allowed': motivoSinDictado(),
        'audio-capture': T.micNoHay,
        network: T.micSinInternet,
      };
      if (graves[tipo]) {
        this.corriendo = false;
        if (ajustes.escucha) ponerEscucha(false);
        this.modo = 'apagado';
        window.clearTimeout(this.temporizador);
        window.clearTimeout(this.revisarAudio);
        quitarInterino();
        this.avisar(graves[tipo]);
        ponerEstado(estadoBase());
        return;
      }
      if (tipo === 'language-not-supported') this.idioma = ajustes.idioma === 'en' ? 'en-US' : 'es-MX';
      if (tipo === 'no-speech' && this.modo === 'comando') {
        this.avisar(T.noTeEscuche);
        this.terminarComando('');
      }
    },

    fin() {
      this.corriendo = false;
      // El navegador deja de escuchar cada cierto tiempo: si sigue encendido, se vuelve a activar.
      if ((this.modo === 'vigilando' || this.modo === 'comando') && !document.hidden) {
        this.reintentos += 1;
        const espera = Math.min(250 * this.reintentos, 5000);
        window.setTimeout(() => {
          if (this.modo !== 'apagado' && !this.corriendo) this.arrancar();
        }, espera);
      }
    },
  };

  function sonar() {
    try {
      const Contexto = window.AudioContext || window.webkitAudioContext;
      if (!Contexto) return;
      const ctx = sonar.ctx || (sonar.ctx = new Contexto());
      const osc = ctx.createOscillator();
      const vol = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(880, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(1320, ctx.currentTime + 0.12);
      vol.gain.setValueAtTime(0.0001, ctx.currentTime);
      vol.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + 0.02);
      vol.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25);
      osc.connect(vol).connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.26);
    } catch (e) { /* sin sonido */ }
  }

  // ── Órdenes que Gotita entiende al instante, sin preguntarle a Claude ──
  function comandoLocal(texto) {
    const t = texto.trim().replace(/[.!¡]+$/g, '');
    const es = (patron) => new RegExp(LOCAL + patron + '$', 'i').test(t);
    const PEDIR = '(?:(?:pon(?:te|le)?|cambia(?:r)?(?: a)?|activa(?:r)?|usa(?:r)?)\\s+)?(?:el\\s+)?';
    const HABLA = '(?:(?:h[aá]blame|habla(?:me)?|resp[oó]nde(?:me)?|cambia(?:r)?|p[aá]sate|pon(?:te)?)\\s+)?(?:(?:en|a|al)\\s+)?';
    if (PARAR.test(t)) return () => voz.parar();
    if (es(PEDIR + 'modo\\s+t[eé]cnico') || es('technical mode')) return () => cambiarModo('tecnico', true);
    if (es(PEDIR + 'modo\\s+(?:sencillo|simple|f[aá]cil)') || es('simple mode')) return () => cambiarModo('sencillo', true);
    if (es('(?:habla(?:me)?\\s+)?m[aá]s\\s+(?:lento|despacio)') || es('(?:speak\\s+)?slower')) {
      return () => cambiarVelocidad('lenta', true);
    }
    if (es('(?:velocidad\\s+normal|habla(?:me)?\\s+normal|m[aá]s\\s+r[aá]pido)') || es('(?:normal speed|faster)')) {
      return () => cambiarVelocidad('normal', true);
    }
    if (es(HABLA + 'ingl[eé]s') || es('(?:(?:speak|talk|answer)(?: to me)?(?: in)?\\s+)?english')) {
      return () => cambiarIdioma('en', true);
    }
    if (es(HABLA + 'espa[nñ]ol') || es('(?:(?:speak|talk|answer)(?: to me)?(?: in)?\\s+)?spanish')) {
      return () => cambiarIdioma('es', true);
    }
    return null;
  }

  /* Idioma en que habla Gotita: cambia su voz, el dictado y el idioma de sus respuestas. */
  function cambiarIdioma(idioma, avisar) {
    if (idioma !== 'es' && idioma !== 'en') return;
    const cambio = idioma !== ajustes.idioma;
    ajustes.idioma = idioma;
    guardar(local, 'idioma', idioma);
    T = TEXTOS[idioma];
    SUGERENCIAS = SUGERENCIAS_POR_IDIOMA[idioma];
    raiz.querySelectorAll('[data-idioma]').forEach((b) => b.setAttribute('aria-checked', b.dataset.idioma === idioma ? 'true' : 'false'));
    voz.elegir();
    oido.idioma = idioma === 'en' ? 'en-US' : 'es-US';
    voz.avisoSinVoz = false;
    if (cambio && oido.corriendo && oido.rec) {
      // Vuelve a escuchar, ahora en el idioma nuevo.
      try { oido.rec.abort(); } catch (e) { /* nada */ }
    }
    el.estado.textContent = T[estado] || T.reposo;
    el.texto.placeholder = T.escribe;
    if (abierto()) pintarSugerencias();
    traducirHistorial();
    if (avisar) confirmar(T.idiomaCambiado);
  }

  function confirmar(texto) {
    agregar('gotita', texto);
    if (!abierto()) mostrarBurbuja(texto, {});
    voz.decir(texto);
  }

  function cambiarModo(modo, avisar) {
    ajustes.modo = modo;
    guardar(local, 'modo', modo);
    raiz.querySelectorAll('[data-modo]').forEach((b) => b.setAttribute('aria-checked', b.dataset.modo === modo ? 'true' : 'false'));
    if (avisar) confirmar(modo === 'tecnico' ? T.modoTecnico : T.modoSencillo);
  }

  function cambiarVelocidad(velocidad, avisar) {
    ajustes.velocidad = velocidad;
    guardar(local, 'velocidad', velocidad);
    raiz.querySelectorAll('[data-velocidad]').forEach((b) => b.setAttribute('aria-checked', b.dataset.velocidad === velocidad ? 'true' : 'false'));
    if (avisar) confirmar(velocidad === 'lenta' ? T.lenta : T.normal);
  }

  function ponerEscucha(activa) {
    ajustes.escucha = !!activa;
    guardar(local, 'escucha', ajustes.escucha);
    el.escucha.checked = ajustes.escucha;
    if (ajustes.escucha) oido.vigilar();
    else { oido.detener(); ponerEstado('reposo'); }
  }

  // ── Preguntarle a Gotita (contesta frase por frase) ──
  let controlador = null;
  let navegacionPendiente = null;

  function cancelarPregunta() {
    if (controlador) { controlador.abort(); controlador = null; }
    mostrarEscribiendo(false);
  }

  function historialParaEnviar() {
    return chat
      .filter((m) => m.rol === 'usuario' || m.rol === 'gotita')
      .slice(-MAX_HISTORIAL)
      .map((m) => ({ rol: m.rol, texto: String(m.texto || '').slice(0, 800) }));
  }

  async function leerEventos(respuesta, manejar) {
    const lector = respuesta.body.getReader();
    const decodificador = new TextDecoder();
    let pendiente = '';
    for (;;) {
      const { value, done } = await lector.read();
      if (done) break;
      pendiente += decodificador.decode(value, { stream: true });
      let corte;
      while ((corte = pendiente.indexOf('\n\n')) >= 0) {
        const bloque = pendiente.slice(0, corte);
        pendiente = pendiente.slice(corte + 2);
        let tipo = 'message';
        let datos = '';
        bloque.split('\n').forEach((linea) => {
          if (linea.startsWith('event:')) tipo = linea.slice(6).trim();
          else if (linea.startsWith('data:')) datos += linea.slice(5).trim();
        });
        if (!datos) continue;
        try { manejar(tipo, JSON.parse(datos)); } catch (e) { /* evento dañado: se ignora */ }
      }
    }
  }

  function ejecutarAccion(accion) {
    if (accion.tipo === 'modo' && (accion.modo === 'sencillo' || accion.modo === 'tecnico')) {
      cambiarModo(accion.modo, false);
    } else if (accion.tipo === 'idioma' && (accion.idioma === 'es' || accion.idioma === 'en')) {
      cambiarIdioma(accion.idioma, false);
    } else if (accion.tipo === 'velocidad' && (accion.velocidad === 'lenta' || accion.velocidad === 'normal')) {
      cambiarVelocidad(accion.velocidad, false);
    } else if (accion.tipo === 'seccion' && typeof accion.seccion === 'string') {
      mostrarSeccion(accion.seccion);
    } else if (accion.tipo === 'navegar') {
      // Solo pantallas permitidas para esta persona.
      const url = DESTINOS[accion.destino];
      if (url && url === accion.url && url !== window.location.pathname) navegacionPendiente = url;
    }
  }

  async function preguntar(texto, opciones) {
    const pregunta = String(texto || '').trim().slice(0, 600);
    if (!pregunta) return;
    const conCaptura = !!(opciones && opciones.captura) || PIDE_PANTALLA.test(pregunta);
    const local = comandoLocal(pregunta);
    if (local) {
      quitarInterino();
      agregar('usuario', pregunta);
      local();
      if (estado === 'escuchando') ponerEstado(estadoBase());
      return;
    }

    cancelarPregunta();
    voz.parar();
    quitarInterino();
    const historial = historialParaEnviar();
    // Con "Oye Gotita" y el chat cerrado no se abre el chat: responde por voz y en el globito, y queda guardado.
    const soloVoz = !!(opciones && opciones.porVoz) && !abierto();
    agregar('usuario', pregunta);
    if (!abierto() && !soloVoz) abrir(false);
    ponerEstado('pensando');
    mostrarEscribiendo(true);

    const cuerpo = { texto: pregunta, modo: ajustes.modo, idioma: ajustes.idioma, pantalla: leerPantalla(conCaptura), historial: historial };
    cuerpo.conversacion = conversacionId; // vacío = conversación nueva
    if (soloVoz) cuerpo.por_voz = true;
    if (opciones && (opciones.idiomaHablado === 'es' || opciones.idiomaHablado === 'en')) cuerpo.idioma_hablado = opciones.idiomaHablado;
    if (conCaptura && !pantallaPrivada()) {
      const captura = await capturarPantalla();
      if (captura) cuerpo.captura = captura;
    }

    const miControlador = new AbortController();
    controlador = miControlador;
    let respuesta;
    try {
      respuesta = await fetch(URL_TURNO, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', 'X-CSRFToken': CSRF },
        body: JSON.stringify(cuerpo),
        signal: miControlador.signal,
      });
    } catch (e) {
      if (miControlador.signal.aborted) return;
      terminarConError(T.sinConexion);
      return;
    }
    if (!respuesta.ok || !respuesta.body) {
      let mensaje = T.error;
      try { mensaje = (await respuesta.json()).error || mensaje; } catch (e) { /* respuesta sin JSON */ }
      terminarConError(mensaje);
      return;
    }

    const mensaje = { rol: 'gotita', texto: '' };
    let nodo = null;
    navegacionPendiente = null;
    try {
      await leerEventos(respuesta, (tipo, datos) => {
        if (controlador !== miControlador) return;
        if (tipo === 'texto' && datos.t) {
          mostrarEscribiendo(false);
          mensaje.texto = mensaje.texto ? mensaje.texto + ' ' + datos.t : datos.t;
          if (!nodo) {
            chat.push(mensaje);
            if (abierto()) { nodo = nodoMensaje(mensaje); el.chat.appendChild(nodo); }
          } else {
            nodo.querySelector('p').textContent = mensaje.texto;
          }
          if (!abierto()) mostrarBurbuja(mensaje.texto, {});
          guardarChat();
          bajarChat();
          voz.decir(datos.t);
        } else if (tipo === 'accion') {
          ejecutarAccion(datos);
        } else if (tipo === 'conversacion' && Number.isInteger(datos.id)) {
          conversacionId = datos.id;
          guardar(sesion, 'conversacion', conversacionId);
          if (!el.historial.hidden) cargarHistorial();
        } else if (tipo === 'error') {
          terminarConError(datos.mensaje || T.error);
        }
      });
    } catch (e) {
      if (!miControlador.signal.aborted) terminarConError(T.sinConexion);
      return;
    }
    if (controlador !== miControlador) return;
    controlador = null;
    marcarActividad();
    mostrarEscribiendo(false);
    if (!voz.hablando && estado === 'pensando') ponerEstado(estadoBase());

    if (navegacionPendiente) {
      const destino = navegacionPendiente;
      navegacionPendiente = null;
      await voz.terminar(6000);
      guardarChat();
      window.location.assign(destino);
    }
  }

  function terminarConError(texto) {
    controlador = null;
    mostrarEscribiendo(false);
    agregar('error', texto);
    if (!abierto()) mostrarBurbuja(texto, {});
    ponerEstado(estadoBase());
  }

  // ── Burbuja ──
  let temporizadorBurbuja = null;
  function mostrarBurbuja(texto, opciones) {
    if (abierto()) return;
    el.burbuja.hidden = false;
    el.burbuja.dataset.tipo = opciones.aviso ? 'aviso' : 'frase';
    el.burbujaTexto.textContent = texto;
    el.burbujaVer.hidden = !opciones.url;
    el.burbujaVer.dataset.url = opciones.url || '';
    window.clearTimeout(temporizadorBurbuja);
    temporizadorBurbuja = window.setTimeout(ocultarBurbuja, opciones.aviso ? 20000 : 12000);
  }
  function ocultarBurbuja() {
    window.clearTimeout(temporizadorBurbuja);
    el.burbuja.hidden = true;
  }
  el.burbuja.addEventListener('mouseenter', () => window.clearTimeout(temporizadorBurbuja));
  el.burbuja.addEventListener('mouseleave', () => { temporizadorBurbuja = window.setTimeout(ocultarBurbuja, 6000); });

  // ── Avisos de sensores y fugas ──
  async function revisarAvisos() {
    if (document.hidden) return;
    let datos;
    try {
      const respuesta = await fetch(URL_AVISOS, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      if (!respuesta.ok) return;
      datos = await respuesta.json();
    } catch (e) { return; }

    const clave = 'avisos:' + USUARIO;
    const memoria = leer(local, clave, null);
    const primera = !memoria;
    const estados = (memoria && memoria.estados) || {};
    const vistas = new Set((memoria && memoria.vistas) || []);
    const nuevos = [];
    (datos.avisos || []).forEach((aviso) => {
      if (!aviso.clave) return;
      if (aviso.tipo === 'sensor') {
        const antes = estados[aviso.clave] || 'Normal';
        const ahora = aviso.estado || 'Normal';
        estados[aviso.clave] = ahora;
        if (!primera && ahora !== 'Normal' && ahora !== antes) nuevos.push(aviso);
      } else if (!vistas.has(aviso.clave)) {
        vistas.add(aviso.clave);
        if (!primera) nuevos.push(aviso);
      }
    });
    guardar(local, clave, { estados: estados, vistas: Array.from(vistas).slice(-200) });
    nuevos.slice(-3).forEach(anunciarAviso);
  }

  function anunciarAviso(aviso) {
    const texto = aviso.gotita || [aviso.titulo, aviso.mensaje].filter(Boolean).join('. ');
    if (!texto) return;
    const url = aviso.url && Object.values(DESTINOS).includes(aviso.url) ? aviso.url : null;
    agregar('aviso', texto, url ? { url: url } : {});
    if (!abierto()) {
      el.punto.hidden = false;
      mostrarBurbuja(texto, { aviso: true, url: url });
    }
    if (ajustes.leerAvisos && estado !== 'pensando' && estado !== 'escuchando') voz.decir(texto);
  }

  // ── Historial de conversaciones (las de los últimos 20 días) ──
  let diasHistorial = 20;

  async function pedirJson(url, metodo) {
    const respuesta = await fetch(url, {
      method: metodo || 'GET',
      credentials: 'same-origin',
      headers: { Accept: 'application/json', 'X-CSRFToken': CSRF },
    });
    let datos = {};
    try { datos = await respuesta.json(); } catch (e) { /* sin JSON */ }
    if (!respuesta.ok || !datos.ok) throw new Error(datos.error || 'error');
    return datos;
  }

  function urlConversacion(id) {
    return URL_HISTORIAL + encodeURIComponent(String(id)) + '/';
  }

  function traducirHistorial() {
    if (!el.historial) return;
    el.historialTitulo.textContent = T.historial;
    el.historialBoton.setAttribute('aria-label', T.verHistorial);
    el.historialBoton.title = T.historial;
    el.historialCerrar.setAttribute('aria-label', T.cerrarHistorial);
    el.nueva.querySelector('span').textContent = T.nueva;
    el.historialNota.textContent = T.notaHistorial.replace('{dias}', diasHistorial);
    el.historialTodo.textContent = T.borrarTodo;
    el.borrar.textContent = T.borrarEsta;
  }

  function fechaCorta(iso) {
    const fecha = new Date(iso);
    if (isNaN(fecha)) return '';
    const hoy = new Date();
    const ayer = new Date(hoy.getFullYear(), hoy.getMonth(), hoy.getDate() - 1);
    const idioma = ajustes.idioma === 'en' ? 'en-US' : 'es-SV';
    if (fecha.toDateString() === hoy.toDateString()) {
      return fecha.toLocaleTimeString(idioma, { hour: 'numeric', minute: '2-digit' });
    }
    if (fecha.toDateString() === ayer.toDateString()) return T.ayer;
    return fecha.toLocaleDateString(idioma, { day: 'numeric', month: 'short' });
  }

  function pintarHistorial(lista) {
    el.historialLista.textContent = '';
    if (!lista.length) {
      const vacio = document.createElement('li');
      vacio.className = 'gotita-historial-vacio';
      vacio.textContent = T.sinHistorial;
      el.historialLista.appendChild(vacio);
      return;
    }
    lista.forEach((item) => {
      const fila = document.createElement('li');
      fila.className = 'gotita-historial-item';
      if (item.id === conversacionId) fila.setAttribute('aria-current', 'true');
      const abrirBoton = document.createElement('button');
      abrirBoton.type = 'button';
      abrirBoton.className = 'gotita-historial-abrir';
      const titulo = document.createElement('strong');
      titulo.textContent = item.titulo;
      const cuando = document.createElement('span');
      cuando.textContent = fechaCorta(item.actualizada);
      abrirBoton.append(titulo, cuando);
      abrirBoton.addEventListener('click', () => abrirConversacion(item.id));
      const borrarBoton = document.createElement('button');
      borrarBoton.type = 'button';
      borrarBoton.className = 'gotita-historial-borrar';
      borrarBoton.setAttribute('aria-label', T.borrarUna + ': ' + item.titulo);
      borrarBoton.title = T.borrarUna;
      borrarBoton.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-gotita-basura"></use></svg>';
      borrarBoton.addEventListener('click', () => borrarConversacion(item.id));
      fila.append(abrirBoton, borrarBoton);
      el.historialLista.appendChild(fila);
    });
  }

  async function cargarHistorial() {
    if (!URL_HISTORIAL) return;
    try {
      const datos = await pedirJson(URL_HISTORIAL);
      diasHistorial = datos.dias || diasHistorial;
      traducirHistorial();
      pintarHistorial(Array.isArray(datos.conversaciones) ? datos.conversaciones : []);
    } catch (e) {
      el.historialLista.textContent = '';
      const error = document.createElement('li');
      error.className = 'gotita-historial-vacio';
      error.textContent = T.historialError;
      el.historialLista.appendChild(error);
    }
  }

  function mostrarHistorial(mostrar) {
    el.historial.hidden = !mostrar;
    el.panel.classList.toggle('con-historial', mostrar);
    el.historialBoton.setAttribute('aria-expanded', mostrar ? 'true' : 'false');
    if (mostrar) cargarHistorial();
  }

  function empezarDeNuevo() {
    voz.parar();
    cancelarPregunta();
    chat = [];
    conversacionId = null;
    guardar(sesion, 'conversacion', null);
    guardarChat();
    pintarChat();
    ponerEstado(estadoBase());
  }

  async function abrirConversacion(id) {
    try {
      const datos = await pedirJson(urlConversacion(id));
      voz.parar();
      cancelarPregunta();
      chat = (datos.mensajes || []).map((m) => ({ rol: m.rol === 'gotita' ? 'gotita' : 'usuario', texto: String(m.texto || '') }));
      conversacionId = datos.id;
      guardar(sesion, 'conversacion', conversacionId);
      guardarChat();
      pintarChat();
      ponerEstado(estadoBase());
      // En el teléfono el historial tapa el chat: se cierra al elegir.
      if (window.matchMedia('(max-width: 599.98px)').matches) mostrarHistorial(false);
      else cargarHistorial();
    } catch (e) {
      agregar('error', T.noSePudoAbrir);
    }
  }

  async function borrarConversacion(id) {
    try { await pedirJson(urlConversacion(id), 'DELETE'); } catch (e) { /* ya no existía */ }
    if (id === conversacionId) empezarDeNuevo();
    cargarHistorial();
  }

  // ── Sugerencias ──
  function pintarSugerencias() {
    el.sugerencias.textContent = '';
    const id = pantallaId();
    const lista = SUGERENCIAS[id] || SUGERENCIAS.otro;
    const explicar = document.createElement('button');
    explicar.type = 'button';
    explicar.className = 'gotita-chip';
    explicar.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-gotita-pantalla"></use></svg>';
    explicar.appendChild(document.createTextNode(T.explicarPantalla));
    explicar.addEventListener('click', () => preguntar(T.explicarPantalla, { captura: true }));
    el.sugerencias.appendChild(explicar);
    lista.forEach((texto) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'gotita-chip';
      chip.textContent = texto;
      chip.addEventListener('click', () => preguntar(texto));
      el.sugerencias.appendChild(chip);
    });
  }

  // ── Eventos de la interfaz ──
  el.boton.addEventListener('click', () => (abierto() ? cerrar() : abrir()));
  el.cerrar.addEventListener('click', () => cerrar());
  el.historialBoton.addEventListener('click', () => mostrarHistorial(el.historial.hidden));
  el.historialCerrar.addEventListener('click', () => mostrarHistorial(false));
  el.nueva.addEventListener('click', () => {
    empezarDeNuevo();
    if (window.matchMedia('(max-width: 599.98px)').matches) mostrarHistorial(false);
    else cargarHistorial();
    el.texto.focus({ preventScroll: true });
  });
  el.historialTodo.addEventListener('click', async () => {
    if (!window.confirm(T.confirmarBorrarTodo)) return;
    try { await pedirJson(URL_HISTORIAL, 'DELETE'); } catch (e) { /* se intenta de nuevo luego */ }
    empezarDeNuevo();
    cargarHistorial();
  });
  // Cualquier cosa que la persona haga en el chat cuenta como que lo está usando.
  ['pointerdown', 'keydown', 'input', 'wheel', 'touchstart'].forEach((tipo) => {
    el.panel.addEventListener(tipo, marcarActividad, { passive: true });
  });
  el.chat.addEventListener('scroll', marcarActividad, { passive: true });
  el.ajustesBoton.addEventListener('click', () => {
    const mostrar = el.ajustes.hidden;
    el.ajustes.hidden = !mostrar;
    el.ajustesBoton.setAttribute('aria-expanded', mostrar ? 'true' : 'false');
  });
  document.addEventListener('keydown', (evento) => {
    if (evento.key === 'Escape' && abierto()) { evento.preventDefault(); cerrar(); }
  });

  el.form.addEventListener('submit', (evento) => {
    evento.preventDefault();
    const texto = el.texto.value;
    el.texto.value = '';
    ajustarAltura();
    preguntar(texto);
  });
  el.texto.addEventListener('keydown', (evento) => {
    if (evento.key === 'Enter' && !evento.shiftKey && !evento.isComposing) {
      evento.preventDefault();
      el.form.requestSubmit();
    }
  });
  function ajustarAltura() {
    el.texto.style.height = 'auto';
    el.texto.style.height = Math.min(el.texto.scrollHeight, 120) + 'px';
  }
  el.texto.addEventListener('input', ajustarAltura);
  el.texto.placeholder = T.escribe;

  el.mic.addEventListener('click', () => {
    if (dictado.grabando) { if (dictado.terminar) dictado.terminar(); return; }
    if (oido.modo === 'comando') { oido.terminarComando(); return; }
    oido.escucharComando();
  });
  el.parar.addEventListener('click', () => { dictado.parar(); voz.parar(); cancelarPregunta(); ponerEstado(estadoBase()); });

  raiz.querySelectorAll('[data-modo]').forEach((b) => b.addEventListener('click', () => cambiarModo(b.dataset.modo, false)));
  raiz.querySelectorAll('[data-velocidad]').forEach((b) => b.addEventListener('click', () => cambiarVelocidad(b.dataset.velocidad, false)));
  raiz.querySelectorAll('[data-idioma]').forEach((b) => b.addEventListener('click', () => cambiarIdioma(b.dataset.idioma, true)));

  el.escucha.addEventListener('change', () => {
    if (el.escucha.checked && (!oido.disponible || NAVEGADOR_SIN_DICTADO || !window.isSecureContext)) {
      el.escucha.checked = false;
      oido.avisar(motivoSinDictado());
      return;
    }
    ponerEscucha(el.escucha.checked);
    // Gotita explica para qué sirve el interruptor al encenderlo o apagarlo.
    confirmar(el.escucha.checked ? T.escuchaEncendida : T.escuchaApagada);
  });
  if (el.vozLista) {
    el.vozLista.addEventListener('change', () => {
      ajustes.voces[ajustes.idioma] = el.vozLista.value;
      guardar(local, 'voces', ajustes.voces);
      voz.elegir();
      voz.parar();
      voz.decir(T.pruebaVoz);
    });
  }
  if (el.vozProbar) {
    el.vozProbar.addEventListener('click', () => {
      voz.parar();
      voz.decir(T.pruebaVoz);
    });
  }
  el.voz.addEventListener('change', () => {
    ajustes.voz = el.voz.checked;
    guardar(local, 'voz', ajustes.voz);
    if (!ajustes.voz) voz.parar();
  });
  el.leerAvisos.addEventListener('change', () => {
    ajustes.leerAvisos = el.leerAvisos.checked;
    guardar(local, 'leer-avisos', ajustes.leerAvisos);
  });
  el.borrar.addEventListener('click', async () => {
    const id = conversacionId;
    if (id) { try { await pedirJson(urlConversacion(id), 'DELETE'); } catch (e) { /* ya no existía */ } }
    empezarDeNuevo();
    if (!el.historial.hidden) cargarHistorial();
    confirmar(T.borrado);
  });

  el.burbujaCerrar.addEventListener('click', ocultarBurbuja);
  el.burbujaAbrir.addEventListener('click', () => abrir());
  el.burbujaEscuchar.addEventListener('click', () => { el.burbujaEscuchar.hidden = true; voz.desbloquear(); });
  el.burbujaVer.addEventListener('click', () => {
    const url = el.burbujaVer.dataset.url;
    if (url && Object.values(DESTINOS).includes(url)) window.location.assign(url);
  });

  // Si la pestaña no está a la vista, Gotita no escucha (por privacidad); al volver, sigue.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (oido.corriendo && oido.rec) { try { oido.rec.abort(); } catch (e) { /* nada */ } }
    } else if (ajustes.escucha) {
      oido.vigilar();
      revisarAvisos();
    }
  });
  window.addEventListener('pagehide', () => { voz.parar(); oido.detener(); });

  // ── Inicio ──
  if (!oido.disponible || NAVEGADOR_SIN_DICTADO || !window.isSecureContext) {
    el.escucha.closest('label').title = motivoSinDictado();
    oido.disponible = false;
  }
  if (!voz.disponible) {
    el.voz.disabled = true;
    ajustes.voz = false;
  }
  el.voz.checked = ajustes.voz;
  el.leerAvisos.checked = ajustes.leerAvisos;
  el.escucha.checked = ajustes.escucha && oido.disponible;
  cambiarModo(ajustes.modo === 'tecnico' ? 'tecnico' : 'sencillo', false);
  cambiarVelocidad(ajustes.velocidad === 'lenta' ? 'lenta' : 'normal', false);
  cambiarIdioma(ajustes.idioma, false);
  el.burbujaVer.textContent = T.ver;
  el.burbujaAbrir.textContent = T.abrirChat;
  ponerEstado('reposo');

  if (ajustes.escucha && oido.disponible) {
    // Solo se enciende sola si ya habías dado permiso al micrófono antes.
    const empezar = () => oido.vigilar();
    if (navigator.permissions && navigator.permissions.query) {
      navigator.permissions.query({ name: 'microphone' }).then((permiso) => {
        if (permiso.state === 'granted') empezar();
        else ponerEscucha(false);
      }).catch(empezar);
    } else {
      empezar();
    }
  }
  // Al pasar a otra pantalla el chat sigue abierto, salvo que ya llevara 2 minutos sin usarse.
  if (leer(sesion, 'abierto', false)) {
    if (Date.now() - ultimaActividad < INACTIVIDAD_MS) abrir(false);
    else guardar(sesion, 'abierto', false);
  }
  traducirHistorial();
  window.setInterval(revisarInactividad, 10000);

  window.setTimeout(revisarAvisos, 4000);
  window.setInterval(revisarAvisos, REVISION_AVISOS_MS);

  window.Gotita = { abrir: abrir, preguntar: preguntar, parar: () => voz.parar() };
}());
