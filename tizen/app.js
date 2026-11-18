// tvdash companion: waits for the service to say which page to show, then becomes that page (full screen, no bars).
//
// The TV does not hand launch arguments to an app, so the service starts the app (REST) and then talks to it over a
// channel on the TV itself: both sides connect to ws://<tv>:8001/api/v2/channels/<CHANNEL> (the app through
// 127.0.0.1) and the service sends {"event": "tvdash.show", "data": {"url": ...}}.
// Tizen's default content security policy only allows files of the app itself: no inline script or style.
const CHANNEL = "com.tvdash.kiosk";
const NAME = "tvdash-app";   // the service looks for this client name before it sends

const status = document.getElementById("status");
const say = (state, text) => { document.body.setAttribute("data-state", state); status.textContent = text; };

function show(url) {
  if (typeof url !== "string" || !/^https?:\/\//i.test(url)) {
    say("refused", "Geweigerd: geen http(s)-adres");
    return;
  }
  say("opening", "Pagina openen: " + url.split("?")[0].replace(/^https?:\/\//i, ""));   // never print the query: it often holds a key
  setTimeout(() => location.replace(url), 400);   // a moment to see the logo react; this page is gone after this
}

function listen() {
  const socket = new WebSocket("ws://127.0.0.1:8001/api/v2/channels/" + CHANNEL + "?name=" + btoa(NAME));
  socket.onopen = () => say("waiting", "Wachten op een pagina");
  socket.onmessage = (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch (error) { return; }
    if (message.event === "tvdash.show" && message.data) show(message.data.url);
  };
  socket.onclose = () => { say("lost", "Verbinding verbroken, opnieuw proberen"); setTimeout(listen, 2000); };
}

listen();
