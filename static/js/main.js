// Theo Property front-end helpers
(function () {
  "use strict";

  // Broken photo (file missing or failed to load): show a neat "no photo" placeholder instead of
  // the browser's broken-image icon and the long alt text.
  const PHOTO_PLACEHOLDER = "data:image/svg+xml," + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600" viewBox="0 0 800 600">' +
    '<rect width="800" height="600" fill="#e7f1ec"/>' +
    '<g fill="none" stroke="#1a8754" stroke-width="14" stroke-linejoin="round" opacity=".45">' +
    '<rect x="290" y="220" width="220" height="160" rx="16"/><circle cx="345" cy="270" r="18"/>' +
    '<path d="M300 365l70-70 50 45 35-30 45 55"/></g>' +
    '<text x="400" y="430" text-anchor="middle" font-family="Inter,Arial,sans-serif" font-size="26" fill="#1a8754" opacity=".7">Photo not available</text></svg>');
  function useFallback(img) {
    if (!img || img.dataset.fallback || img.src.startsWith("data:")) return;
    img.dataset.fallback = "1";
    img.alt = "";
    img.src = PHOTO_PLACEHOLDER;
  }
  document.addEventListener("error", function (e) {
    if (e.target && e.target.tagName === "IMG") useFallback(e.target);
  }, true);  // capture: image errors don't bubble
  // Images that already failed before this script ran
  document.querySelectorAll("img").forEach(function (img) {
    // (lazy images that haven't started loading are skipped: the error listener covers them later)
    if (img.loading !== "lazy" && img.complete && img.naturalWidth === 0 && img.getAttribute("src")) useFallback(img);
  });

  function getCookie(name) {
    const m = document.cookie.match(new RegExp("(^|;\\s*)" + name + "=([^;]*)"));
    return m ? decodeURIComponent(m[2]) : null;
  }

  // Favorite toggle with Fetch (falls back to normal form submit without JS)
  document.addEventListener("submit", async function (e) {
    const form = e.target.closest("form.js-fav");
    if (!form) return;
    e.preventDefault();
    try {
      const res = await fetch(form.action, {
        method: "POST",
        headers: { "X-CSRFToken": getCookie("csrftoken"), "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      });
      if (res.redirected || res.status === 403) { window.location = form.action; return; }
      const data = await res.json();
      form.querySelectorAll("button").forEach(function (btn) {
        btn.classList.toggle("active", data.favorited);
        const icon = btn.querySelector("i");
        if (icon) icon.className = data.favorited ? "fa-solid fa-heart" : "fa-regular fa-heart";
        const label = btn.querySelector(".fav-label");
        if (label) label.textContent = data.favorited ? "Saved" : "Save";
      });
    } catch (err) { form.submit(); }
  });

  // Share a property: native share sheet on phones, copy link elsewhere
  function toast(text) {
    const t = document.createElement("div");
    t.className = "tp-toast"; t.setAttribute("role", "status"); t.textContent = text;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2200);
  }
  document.addEventListener("click", async function (e) {
    const btn = e.target.closest(".js-share");
    if (!btn) return;
    e.preventDefault();
    const url = btn.dataset.url, title = btn.dataset.title;
    if (navigator.share) {
      try { await navigator.share({ title: title, text: title, url: url }); } catch (err) { /* cancelled */ }
      return;
    }
    try { await navigator.clipboard.writeText(url); toast("Link copied"); }
    catch (err) { window.prompt("Copy this link:", url); }
  });

  // Sign In: show / hide password, and the pop-up signs in without leaving the page
  document.addEventListener("click", function (e) {
    const btn = e.target.closest("[data-toggle-password]");
    if (!btn) return;
    const input = document.getElementById(btn.dataset.togglePassword);
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
    btn.innerHTML = show ? '<i class="fa-regular fa-eye-slash"></i>' : '<i class="fa-regular fa-eye"></i>';
  });
  const signInModal = document.getElementById("signInModal");
  if (signInModal) {
    const form = signInModal.querySelector("[data-login-form]");
    const errBox = form.querySelector("[data-login-error]");
    const submit = form.querySelector("[data-login-submit]");
    const showError = function (text) {
      errBox.querySelector("span").textContent = text;
      errBox.hidden = false;
      form.querySelectorAll(".login-input").forEach(el => el.classList.add("has-error"));
    };
    signInModal.addEventListener("shown.bs.modal", function () {
      const user = form.querySelector("[name=username]");
      (user.value ? form.querySelector("[name=password]") : user).focus({ preventScroll: true });
    });
    form.addEventListener("input", function () {
      form.querySelectorAll(".login-input").forEach(el => el.classList.remove("has-error"));
    });
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      if (!form.username.value.trim() || !form.password.value) return showError("Please enter your username or email, and your password.");
      submit.disabled = true;
      const label = submit.innerHTML;
      submit.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Signing in…';
      try {
        const res = await fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin" });
        const loginPath = new URL(form.action, location.href).pathname;
        if (res.redirected && new URL(res.url).pathname !== loginPath) { location.href = res.url; return; }  // signed in
        const page = new DOMParser().parseFromString(await res.text(), "text/html");
        const msg = page.querySelector("[data-login-error]:not([hidden]) span");
        showError(msg && msg.textContent.trim() ? msg.textContent.trim() : "Sign in failed. Please try again.");
        form.password.value = "";
        form.password.focus({ preventScroll: true });
      } catch (err) {
        showError("Connection problem. Please check your internet and try again.");
      } finally {
        submit.disabled = false;
        submit.innerHTML = label;
      }
    });
  }

  // Live search: suggestions while typing (places + properties), keyboard friendly. No search history is kept.
  try { localStorage.removeItem("tp_recent_searches"); } catch (e) {}  // wipe history saved by the earlier version
  document.querySelectorAll(".live-search").forEach(function (form) {
    const input = form.querySelector("input[name=q]");
    const list = form.querySelector(".live-search-list");
    const clearBtn = form.querySelector(".live-search-clear");
    let timer = null, controller = null, items = [], active = -1;

    const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const words = q => q.toLowerCase().split(/\s+/).filter(w => w.length > 1);
    function highlight(text, q) {  // escape first, then mark the typed words
      let html = esc(text);
      words(q).forEach(w => {
        const safe = esc(w).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        html = html.replace(new RegExp("(" + safe + ")", "ig"), "<mark>$1</mark>");
      });
      return html;
    }
    const listUrl = params => form.dataset.listUrl + "?" + new URLSearchParams(params).toString();

    function open(html) {
      list.innerHTML = html; list.hidden = !html;
      input.setAttribute("aria-expanded", html ? "true" : "false");
      items = [...list.querySelectorAll(".ls-item")]; active = -1;
    }
    function close() { open(""); }
    function setActive(i) {
      items.forEach(el => el.classList.remove("active"));
      active = i;
      const el = items[i];
      if (el) {  // scroll only the dropdown, never the page
        el.classList.add("active");
        if (el.offsetTop < list.scrollTop) list.scrollTop = el.offsetTop;
        else if (el.offsetTop + el.offsetHeight > list.scrollTop + list.clientHeight)
          list.scrollTop = el.offsetTop + el.offsetHeight - list.clientHeight;
      }
    }

    async function suggest(q) {
      if (controller) controller.abort();
      controller = new AbortController();
      try {
        const res = await fetch(form.dataset.suggestUrl + "?q=" + encodeURIComponent(q), { signal: controller.signal });
        const data = await res.json();
        if (input.value.trim() !== q) return;  // the visitor kept typing
        let html = "";
        if (data.places.length) {
          html += '<div class="ls-head">Places</div>' + data.places.map(p =>
            `<a class="ls-item" role="option" href="${esc(listUrl({ location: p.name }))}" data-q="${esc(p.name)}">
               <i class="fa-solid fa-location-dot ls-ico"></i>
               <span class="ls-text ls-place">${highlight(p.name, q)}</span>
               <span class="ls-sub">${p.count} listing${p.count === 1 ? "" : "s"}</span></a>`).join("");
        }
        if (data.properties.length) {
          html += '<div class="ls-head">Properties</div>' + data.properties.map(p =>
            `<a class="ls-item" role="option" href="${esc(p.url)}" data-q="${esc(q)}">
               ${p.image ? `<img src="${esc(p.image)}" alt="" loading="lazy">` : '<span class="ls-noimg"><i class="fa-regular fa-image"></i></span>'}
               <span class="ls-text"><span class="ls-title d-block">${highlight(p.title, q)}</span>
                 <span class="ls-sub d-block">${esc(p.price)} · ${esc(p.type)} · ${esc(p.purpose)}</span>
                 <span class="ls-sub d-block"><i class="fa-solid fa-location-dot me-1"></i>${esc(p.location)} · ${esc(p.reference)}</span></span></a>`).join("");
        }
        if (data.total) {
          html += `<a class="ls-item ls-all" role="option" href="${esc(listUrl({ q }))}" data-q="${esc(q)}">
                     <i class="fa-solid fa-magnifying-glass ls-ico"></i>See all ${data.total} result${data.total === 1 ? "" : "s"} for “${esc(q)}”</a>`;
        } else if (!data.places.length) {
          html = `<div class="ls-empty">No properties match “${esc(q)}”. Try a place (e.g. Kicukiro) or a type (house, plot).</div>`;
        }
        open(html);
      } catch (e) { if (e.name !== "AbortError") close(); }
    }

    input.addEventListener("input", function () {
      const q = input.value.trim();
      clearBtn.hidden = !input.value;
      clearTimeout(timer);
      if (q.length < 2) { close(); return; }
      timer = setTimeout(() => suggest(q), 180);  // wait for a short pause in typing
    });
    input.addEventListener("focus", function () {
      const q = input.value.trim();
      if (q.length >= 2) suggest(q);
    });
    input.addEventListener("keydown", function (e) {
      if (list.hidden) return;
      if (e.key === "ArrowDown") { e.preventDefault(); setActive(Math.min(active + 1, items.length - 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setActive(Math.max(active - 1, -1)); }
      else if (e.key === "Escape") { close(); }
      else if (e.key === "Enter" && items[active]) {
        e.preventDefault(); location.href = items[active].href;
      }
    });
    list.addEventListener("mousedown", e => e.preventDefault());  // keep focus in the box while clicking
    clearBtn.addEventListener("click", function () { input.value = ""; clearBtn.hidden = true; input.focus(); close(); });
    form.addEventListener("submit", function (e) {
      if (!input.value.trim()) e.preventDefault();
    });
    document.addEventListener("click", e => { if (!form.contains(e.target)) close(); });
    clearBtn.hidden = !input.value;
  });

  // Property gallery: main photo slides; thumbnails follow it; clicking opens the auto-sliding viewer
  const galleryMain = document.getElementById("galleryMain");
  const galleryModal = document.getElementById("galleryModal");
  if (galleryMain && galleryModal && window.bootstrap) {
    const thumbs = document.querySelectorAll("[data-gallery-thumb]");
    const strip = document.querySelector(".gallery-thumbs");
    galleryMain.addEventListener("slide.bs.carousel", function (e) {
      thumbs.forEach((t, i) => t.classList.toggle("active", i === e.to));
      // Scroll only the thumbnail strip sideways. Never scroll the page: the visitor controls that.
      const t = thumbs[e.to];
      if (strip && t) {
        const left = t.offsetLeft - (strip.clientWidth - t.clientWidth) / 2;  // strip is position:relative
        strip.scrollTo({ left: Math.max(left, 0), behavior: "smooth" });
      }
    });
    const viewerEl = document.getElementById("galleryViewer");
    const viewer = bootstrap.Carousel.getOrCreateInstance(viewerEl);
    const mainCarousel = bootstrap.Carousel.getOrCreateInstance(galleryMain);
    const modal = bootstrap.Modal.getOrCreateInstance(galleryModal);
    const multiple = galleryMain.querySelectorAll(".carousel-item").length > 1;
    if (multiple) mainCarousel.cycle();  // page photos slide on their own while the client reads the details
    const currentIndex = () => [...galleryMain.querySelectorAll(".carousel-item")].findIndex(el => el.classList.contains("active"));
    document.querySelectorAll("[data-gallery-open]").forEach(function (el) {
      el.addEventListener("click", function () {
        const idx = el.dataset.galleryOpen === "current" ? currentIndex() : Number(el.dataset.galleryOpen);
        // Jump to the clicked photo without animating, then let it slide on its own
        viewerEl.querySelectorAll(".carousel-item").forEach((it, i) => it.classList.toggle("active", i === Math.max(idx, 0)));
        modal.show();
      });
    });
    galleryModal.addEventListener("show.bs.modal", () => mainCarousel.pause());
    galleryModal.addEventListener("shown.bs.modal", () => { if (multiple) viewer.cycle(); });
    galleryModal.addEventListener("hide.bs.modal", () => viewer.pause());
    // Keep the page photo in step with where the viewer stopped, then keep sliding
    galleryModal.addEventListener("hidden.bs.modal", function () {
      const idx = [...viewerEl.querySelectorAll(".carousel-item")].findIndex(el => el.classList.contains("active"));
      if (idx >= 0) mainCarousel.to(idx);
      if (multiple) mainCarousel.cycle();
    });
  }

  // Dashboard sidebar toggle (mobile)
  const toggle = document.getElementById("sidebarToggle");
  if (toggle) toggle.addEventListener("click", () => document.querySelector(".dash-sidebar").classList.toggle("open"));

  // Confirm dangerous actions
  document.addEventListener("submit", function (e) {
    const msg = e.target.dataset.confirm;
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // Image upload preview
  const imgInput = document.querySelector("input[type=file][multiple]");
  const preview = document.getElementById("upload-preview");
  if (imgInput && preview) {
    imgInput.addEventListener("change", function () {
      preview.innerHTML = "";
      Array.from(imgInput.files).forEach(function (f) {
        const col = document.createElement("div");
        col.className = "col-4 col-md-2";
        const img = document.createElement("img");
        img.className = "img-fluid rounded";
        img.style.width = "100%"; img.style.aspectRatio = "4/3"; img.style.objectFit = "cover";
        img.src = URL.createObjectURL(f);
        col.appendChild(img); preview.appendChild(col);
      });
    });
  }
})();

/* ---------- Maps ----------
 * Uses Google Maps when GOOGLE_MAPS_API_KEY is set, otherwise Leaflet + OpenStreetMap (no key needed).
 */
window.TPMap = {
  kigali: [-1.9441, 30.0619],

  show(elId, points, opts) {
    const el = document.getElementById(elId);
    if (!el || !window.L) return;
    const map = L.map(el).setView(points.length ? [points[0].lat, points[0].lng] : this.kigali, (opts && opts.zoom) || 13);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors", maxZoom: 19,
    }).addTo(map);
    const markers = points.map(function (p) {
      const m = L.marker([p.lat, p.lng]).addTo(map);
      if (p.title) {
        const div = document.createElement("div");
        const a = document.createElement(p.url ? "a" : "strong");
        if (p.url) a.href = p.url;
        a.textContent = p.title;
        div.appendChild(a);
        if (p.price) { div.appendChild(document.createElement("br")); div.appendChild(document.createTextNode(p.price)); }
        m.bindPopup(div);
      }
      return m;
    });
    if (markers.length > 1) map.fitBounds(L.featureGroup(markers).getBounds().pad(0.2));
    return map;
  },

  // Click on the map (or drag the marker) to set latitude/longitude fields in the property form
  picker(elId, latInput, lngInput) {
    const el = document.getElementById(elId);
    const lat = document.getElementById(latInput), lng = document.getElementById(lngInput);
    if (!el || !window.L || !lat || !lng) return;
    const start = lat.value && lng.value ? [parseFloat(lat.value), parseFloat(lng.value)] : this.kigali;
    const map = L.map(el).setView(start, 13);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "&copy; OpenStreetMap" }).addTo(map);
    const marker = L.marker(start, { draggable: true }).addTo(map);
    function set(ll) { lat.value = ll.lat.toFixed(6); lng.value = ll.lng.toFixed(6); marker.setLatLng(ll); }
    map.on("click", e => set(e.latlng));
    marker.on("dragend", () => set(marker.getLatLng()));
    setTimeout(() => map.invalidateSize(), 300);
  },
};

// Google Places autocomplete for the address field (only when the Google script is loaded)
window.initPlaces = function () {
  const input = document.getElementById("id_address");
  if (!input || !window.google) return;
  const ac = new google.maps.places.Autocomplete(input, { componentRestrictions: { country: "rw" } });
  ac.addListener("place_changed", function () {
    const place = ac.getPlace();
    if (!place.geometry) return;
    document.getElementById("id_latitude").value = place.geometry.location.lat().toFixed(6);
    document.getElementById("id_longitude").value = place.geometry.location.lng().toFixed(6);
  });
};

// Back buttons: go to the previous page of THIS website; if the visitor came from elsewhere
// (Google, WhatsApp link...) or opened the page directly, go to the button's safe fallback link.
(function () {
  "use strict";
  document.addEventListener("click", function (e) {
    const btn = e.target.closest("[data-back]");
    if (!btn || e.ctrlKey || e.metaKey || e.shiftKey) return;
    let sameSite = false;
    try { sameSite = document.referrer && new URL(document.referrer).origin === location.origin; } catch (err) {}
    if (sameSite && history.length > 1 && document.referrer !== location.href) {
      e.preventDefault();
      history.back();
    }
  });

  // If the browser restores a private page from memory (Back after logout), reload it from the
  // server, which checks the login again.
  window.addEventListener("pageshow", function (e) {
    if (e.persisted && document.body.classList.contains("dash-body")) location.reload();
  });
})();

// "Agent replies" chat box: opens over the page; on a property page the visitor asks the agent and
// reads the replies right here (checked every few seconds while the box is open).
(function () {
  const toggle = document.getElementById("chatToggle");
  const panel = document.getElementById("chatPanel");
  if (!toggle || !panel) return;
  const cookie = n => { const m = document.cookie.match(new RegExp("(^|;\s*)" + n + "=([^;]*)")); return m ? decodeURIComponent(m[2]) : ""; };
  const csrf = () => cookie("csrftoken") || (document.querySelector("input[name=csrfmiddlewaretoken]") || {}).value || "";
  let pollTimer = null, conversation = null, lastCount = 0;

  function setOpen(open) {
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      const field = panel.querySelector("#chatName:not([value]), #chatName[value=''], #chatMessage");
      if (field) field.focus({ preventScroll: true });
      startPolling();
      const badge = toggle.querySelector(".messages-badge");
      if (badge && conversation) badge.remove();
    } else {
      clearInterval(pollTimer); pollTimer = null;
    }
  }
  toggle.addEventListener("click", function (e) { e.preventDefault(); setOpen(panel.hidden); });
  panel.querySelector("[data-chat-close]").addEventListener("click", () => { setOpen(false); toggle.focus(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !panel.hidden) setOpen(false); });

  const form = document.getElementById("chatForm");
  if (!form) return;  // not on a property page: the box only lists conversations
  const thread = document.getElementById("chatThread");
  const start = document.getElementById("chatStart");
  const intro = document.getElementById("chatIntro");
  const message = document.getElementById("chatMessage");
  const error = document.getElementById("chatError");
  const body = document.getElementById("chatBody");

  function render(data) {
    conversation = data.conversation;
    if (data.messages.length === lastCount) return;
    lastCount = data.messages.length;
    thread.replaceChildren(...data.messages.map(function (m) {
      const wrap = document.createElement("div");
      wrap.className = "chat-msg " + (m.mine ? "mine" : "theirs");
      const meta = document.createElement("div");
      meta.className = "chat-meta"; meta.textContent = m.who + " · " + m.time;
      const bubble = document.createElement("div");
      bubble.className = "chat-bubble"; bubble.textContent = m.text;  // text only: never HTML
      wrap.append(meta, bubble);
      return wrap;
    }));
    if (!data.messages.some(m => !m.mine)) {
      const wait = document.createElement("div");
      wait.className = "small text-muted";
      wait.textContent = "Sent. The agent will reply here" + (data.agent ? " (" + data.agent + ")" : "") + ". You can also use WhatsApp below.";
      thread.append(wait);
    }
    start.hidden = true; intro.hidden = true;
    body.scrollTop = body.scrollHeight;
  }
  const initial = document.getElementById("chatData");
  const initialData = initial ? JSON.parse(initial.textContent) : null;
  if (initialData) render(initialData);

  function startPolling() {
    if (pollTimer || !conversation) return;
    pollTimer = setInterval(async function () {
      if (panel.hidden || !conversation) return;
      try {
        const res = await fetch(panel.dataset.pollUrl.replace("/0/", "/" + conversation + "/"), { headers: { "X-Requested-With": "XMLHttpRequest" } });
        if (res.ok) render(await res.json());
      } catch (e) { /* offline for a moment: try again next time */ }
    }, 8000);
  }

  panel.querySelectorAll(".chat-chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      message.value = chip.textContent.trim();
      panel.querySelectorAll(".chat-chip").forEach(c => c.classList.toggle("active", c === chip));
      message.focus();
    });
  });
  message.addEventListener("keydown", function (e) {  // Enter sends, Shift+Enter = new line
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    error.hidden = true;
    const text = message.value.trim();
    const name = document.getElementById("chatName");
    if (!conversation && !name.value.trim()) { error.textContent = "Please enter your name."; error.hidden = false; name.focus(); return; }
    if (!text) { error.textContent = "Please write a message."; error.hidden = false; message.focus(); return; }
    const data = new FormData();
    data.append("message", text);
    if (!conversation) {
      data.append("name", name.value.trim());
      data.append("contact", document.getElementById("chatContact").value.trim());
    }
    const button = form.querySelector(".chat-send");
    button.disabled = true;
    try {
      const res = await fetch(panel.dataset.sendUrl, { method: "POST", body: data,
        headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" } });
      const json = await res.json().catch(() => ({ ok: false, error: "Something went wrong. Please try again." }));
      if (!json.ok) { error.textContent = json.error || "Could not send. Please try again."; error.hidden = false; return; }
      message.value = "";
      lastCount = -1; render(json);
      startPolling();
    } catch (err) {
      error.textContent = "Connection problem. Please check your internet and try again."; error.hidden = false;
    } finally {
      button.disabled = false;
    }
  });
})();

// WhatsApp without WhatsApp's "Open app / Continue to WhatsApp Web" choice page:
// phones open the WhatsApp app directly; computers open the chat in WhatsApp Web, ready to send.
window.TPWhatsApp = (function () {
  const isPhone = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
  function number(raw) {
    let d = String(raw || "").replace(/\D/g, "");
    if (d.startsWith("00")) d = d.slice(2);
    if (d.length === 10 && d.startsWith("07")) d = "250" + d.slice(1);   // 0788 123 456 -> 250788123456
    else if (d.length === 9 && d.startsWith("7")) d = "250" + d;
    return d;
  }
  function parse(href) {
    try {
      const u = new URL(href, location.href);
      if (u.hostname !== "wa.me" && u.hostname !== "api.whatsapp.com") return null;
      return { phone: number(u.pathname.replace(/\//g, "") || u.searchParams.get("phone")), text: u.searchParams.get("text") || "" };
    } catch (e) { return null; }
  }
  function query(t) {
    const q = new URLSearchParams();
    if (t.phone) q.set("phone", t.phone);
    if (t.text) q.set("text", t.text);
    return q.toString();
  }
  function open(t, newTab) {
    if (isPhone) {
      // The app opens straight away. If WhatsApp isn't installed, nothing opens: fall back to wa.me.
      const fallback = setTimeout(function () {
        if (!document.hidden) location.href = "https://wa.me/" + t.phone + (t.text ? "?text=" + encodeURIComponent(t.text) : "");
      }, 1800);
      document.addEventListener("visibilitychange", () => clearTimeout(fallback), { once: true });
      location.href = "whatsapp://send?" + query(t);
    } else {
      const url = "https://web.whatsapp.com/send?" + query(t);
      const win = newTab ? window.open(url, "_blank", "noopener") : null;
      if (!win) location.href = url;
    }
  }
  document.addEventListener("click", function (e) {
    const a = e.target.closest("a[href]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const t = parse(a.href);
    if (!t) return;
    e.preventDefault();
    open(t, a.target === "_blank");
  });
  return { open: open, parse: parse };
})();

// Property page "Send on WhatsApp": the message is saved for the agent, then WhatsApp opens
// directly (same as above). Without JavaScript the form still works (server redirect to wa.me).
document.querySelectorAll("form.js-wa-inquiry").forEach(function (form) {
  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    const button = form.querySelector("button[type=submit], button:not([type])");
    const note = form.querySelector(".js-wa-note");
    form.querySelectorAll(".js-wa-error").forEach(el => el.remove());
    button.disabled = true;
    try {
      const res = await fetch(form.action, { method: "POST", body: new FormData(form),
        headers: { "X-Requested-With": "XMLHttpRequest" } });
      const data = await res.json();
      if (!data.ok) {
        const err = document.createElement("div");
        err.className = "alert alert-danger small py-2 mt-2 js-wa-error";
        err.textContent = data.error || "Your message was not sent. Please check the form.";
        button.after(err);
        return;
      }
      if (note) { note.hidden = false; note.textContent = data.notice; }
      form.querySelector("textarea[name=message]").value = "";
      const t = window.TPWhatsApp.parse(data.whatsapp);
      if (t) window.TPWhatsApp.open(t, true);
    } catch (err) {
      form.submit();  // connection hiccup: fall back to the normal way
    } finally {
      button.disabled = false;
    }
  });
});
