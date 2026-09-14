/* Enhancement only. The page is complete without any of this.
   Everything is kept in localStorage; there is no account and no server. */
(function () {
  "use strict";
  var page = location.pathname;
  var blocks = [].slice.call(document.querySelectorAll(".block[data-start]"));
  var holder = document.querySelector(".video[data-youtube]");
  var player = null, timer = null;

  /* ---- 1. the player, built when it is asked for ----------------------------
     Two things were wrong here. The iframe was created as soon as the page
     opened, so every visit contacted YouTube whether or not anyone pressed
     play — the opposite of what the comment claimed. And `enablejsapi=1` was
     passed without the `origin` parameter it requires, which is what makes a
     player refuse to start and show "This video is unavailable" over a video
     that is public and embeddable.

     Now nothing is requested until the play button is pressed, and the
     parameters are complete when it is. */
  /* youtube-nocookie sets no tracking cookie until playback starts. It is
     occasionally stricter than the ordinary host about what it will play; if
     a recording refuses to start, change this to "www.youtube.com". */
  var EMBED_HOST = "www.youtube-nocookie.com";

  function embedUrl(id) {
    /* As few parameters as the page can get away with. `enablejsapi` is only
       needed to drive a synced transcript, and it drags `origin` along with
       it — 1324 of 1337 pages have no transcript and were asking for both for
       nothing, which is what stopped their recordings from playing. */
    var src = "https://" + EMBED_HOST + "/embed/" + id + "?rel=0&playsinline=1";
    /* enablejsapi drives the synced transcript and is also the only way to
       hear that a recording was refused. It requires `origin` alongside it;
       a page opened from disk has an origin of "null", which YouTube rejects,
       so both are left off there and the player is simply left alone. */
    if (location.origin && location.origin !== "null") {
      src += "&enablejsapi=1&origin=" + encodeURIComponent(location.origin);
    }
    return src;
  }

  /* Built through YouTube's own API when it is available, because that is the
     only thing that reports a refusal. Without the API — blocked, offline,
     opened from disk — a plain iframe is used instead and the page is exactly
     as it was before. */
  var apiReady = false;

  /* ---- the dock -------------------------------------------------------------
     The player is never a child of the page content. Moving an iframe in the
     DOM reloads it, so anything that re-parents the player stops the
     recording — which is exactly what carrying it to the next sitting would
     do. Instead it lives in one element at the end of <body> and is put over
     the placeholder in the page, which the stylesheet keeps sticky by itself.
     Navigating swaps the page around it and never touches it. */
  var dock = document.getElementById("dock");
  if (!dock) {
    dock = document.createElement("div");
    dock.id = "dock";
    dock.hidden = true;
    document.body.appendChild(dock);
  }
  var floating = false, ticking = false, playingId = null;

  function place() {
    ticking = false;
    if (dock.hidden) return;
    if (floating || !holder || !document.contains(holder)) {
      dock.classList.add("afloat");
      dock.style.cssText = "";
      return;
    }
    var box = holder.getBoundingClientRect();
    /* The placeholder has scrolled away entirely — let go and float. */
    if (box.bottom < 8 || box.top > window.innerHeight - 8) {
      dock.classList.add("afloat");
      dock.style.cssText = "";
      return;
    }
    dock.classList.remove("afloat");
    dock.style.cssText = "position:fixed;left:" + box.left + "px;top:" + box.top +
                         "px;width:" + box.width + "px;height:" + box.height + "px";
  }

  function reposition() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(place);
  }
  window.addEventListener("scroll", reposition, { passive: true });
  window.addEventListener("resize", reposition, { passive: true });

  function dockChrome(title, href) {
    var bar = document.createElement("p");
    bar.className = "dock-bar";
    bar.innerHTML = '<a class="dock-title" href="' + href + '"></a>' +
                    '<button type="button" class="dock-close" ' +
                    'aria-label="Close the recording">&times;</button>';
    bar.querySelector(".dock-title").textContent = title;
    bar.querySelector(".dock-close").addEventListener("click", stop);
    return bar;
  }

  function stop() {
    try { if (player && player.destroy) player.destroy(); } catch (err) {}
    player = null;
    playingId = null;
    clearInterval(timer);
    dock.hidden = true;
    dock.innerHTML = "";
    dock.classList.remove("afloat");
    dock.style.cssText = "";
    if (holder) holder.classList.remove("is-playing");
  }

  function play(autoplay, seconds) {
    var id = holder.getAttribute("data-youtube");
    playingId = id;
    holder.classList.add("is-playing");
    dock.hidden = false;
    dock.innerHTML = "";
    /* The whole path, not the file name. Once you have navigated away the
       link is being resolved against a different directory, and a bare
       "2000-11-06-….html" points at nothing. */
    dock.appendChild(dockChrome(document.title, location.pathname));
    var shell = document.createElement("div");
    shell.className = "dock-frame";
    dock.appendChild(shell);
    floating = false;
    place();

    if (apiReady && window.YT && YT.Player) {
      var mount = document.createElement("div");
      mount.id = "player-mount";      /* YT.Player needs an element with an id */
      shell.appendChild(mount);
      player = new YT.Player(mount, {
        videoId: id,
        playerVars: { rel: 0, playsinline: 1, autoplay: autoplay ? 1 : 0,
                      start: seconds ? Math.floor(seconds) : 0 },
        events: { onReady: watch, onStateChange: watch, onError: refused }
      });
      return null;
    }
    var frame = document.createElement("iframe");
    frame.src = embedUrl(id) + (autoplay ? "&autoplay=1" : "") +
                (seconds ? "&start=" + Math.floor(seconds) : "");
    frame.title = "Recording";
    frame.allow = "accelerometer; autoplay; encrypted-media; picture-in-picture";
    frame.allowFullscreen = true;
    shell.appendChild(frame);
    return frame;
  }

  /* Some recordings will not play outside YouTube however they are embedded —
     a bare iframe with no parameters at all is refused just the same. Nothing
     in the video's public metadata says so, so it can only be discovered by
     the player failing. Rather than leave YouTube's black "This video is
     unavailable" box sitting in the page, say what happened and send the
     reader where it does play.

     101 and 150 both mean the owner or a rights holder has disallowed
     embedding; 2 and 5 are a bad id and a playback failure. */
  function refused(event) {
    var code = event && event.data;
    if (code !== 101 && code !== 150 && code !== 2 && code !== 5) return;
    var id = holder.getAttribute("data-youtube");
    stop();
    holder.classList.add("elsewhere");
    holder.innerHTML =
      '<p>This recording plays on YouTube but cannot be shown here. ' +
      'That is a restriction on the video itself, not on this page.</p>' +
      '<p><a href="https://www.youtube.com/watch?v=' + id + '">Watch it on YouTube</a></p>';
  }

  if (holder) {
    var start = document.createElement("button");
    start.type = "button";
    start.className = "play";
    start.setAttribute("aria-label", "Play the recording");
    start.innerHTML = '<span class="play-mark" aria-hidden="true"></span>';
    start.addEventListener("click", function () { play(true); });
    holder.appendChild(start);

    /* Loaded here, above the transcript-only code below, because most pages
       have no transcript and returned early — so the API never arrived and a
       refused recording could never be noticed. */
    var tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(tag);
    window.onYouTubeIframeAPIReady = function () { apiReady = true; };
  }

  /* ---- carrying it to the next sitting --------------------------------------
     A link is followed by fetching the next page and swapping what is inside
     <main>. The dock is outside <main>, so the recording is never touched and
     keeps playing while you read ahead. Anything that goes wrong here falls
     through to an ordinary page load, which is what the browser would have
     done anyway. */
  function samePage(href) {
    try {
      var url = new URL(href, location.href);
      return url.origin === location.origin && /\.html$/.test(url.pathname);
    } catch (err) { return false; }
  }

  function swap(url, push) {
    return fetch(url, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var fresh = doc.querySelector("main");
        var here = document.querySelector("main");
        if (!fresh || !here) return Promise.reject("no main");
        here.replaceWith(fresh);
        document.title = doc.title;
        if (push) history.pushState({}, "", url);
        window.scrollTo(0, 0);
        bind();
        /* The recording carries over. On any other page it floats; come back
           to the page it belongs to and it settles into place again. */
        if (playingId) {
          var home = document.querySelector('.video[data-youtube="' + playingId + '"]');
          floating = !home;
          if (home) home.classList.add("is-playing");
          place();
        }
        return true;
      });
  }

  document.addEventListener("click", function (ev) {
    if (ev.defaultPrevented || ev.button || ev.metaKey || ev.ctrlKey ||
        ev.shiftKey || ev.altKey) return;
    var a = ev.target.closest && ev.target.closest("a[href]");
    if (!a || a.target || a.hasAttribute("download") || !samePage(a.href)) return;
    ev.preventDefault();
    swap(a.href, true).catch(function () { location.href = a.href; });
  });

  window.addEventListener("popstate", function () {
    swap(location.href, false).catch(function () { location.reload(); });
  });

  /* Everything below depends on the page currently in <main>, so it is run
     again after a swap. */
  function bind() {
    page = location.pathname;
    blocks = [].slice.call(document.querySelectorAll(".block[data-start]"));
    holder = document.querySelector(".video[data-youtube]");
    if (holder && !holder.querySelector("button.play")) addPlayButton();
    bindTranscript();
    place();
    resumeFromHash();
  }

  function addPlayButton() {
    var start = document.createElement("button");
    start.type = "button";
    start.className = "play";
    start.setAttribute("aria-label", "Play the recording");
    start.innerHTML = '<span class="play-mark" aria-hidden="true"></span>';
    start.addEventListener("click", function () { play(true); });
    holder.appendChild(start);
  }

  function bindTranscript() {
    if (!blocks.length) return;
    wireTimestamps();
    KEY = "avlokan:pos:" + page;
    MARKS = "avlokan:marks:" + page;
    live = null;
    toolbar();
    restoreMarks();
  }

  var KEY = "avlokan:pos:" + page;
  var MARKS = "avlokan:marks:" + page;

  /* ---- the book's index ---------------------------------------------------
     The index is already complete in the page and every link already works.
     All this adds is telling you where you are in it — which of a hundred and
     fourteen aphorisms is on the screen — and keeping that entry in view in a
     column that is itself scrollable. */
  (function () {
    var index = document.querySelector(".book-index");
    var marks = index && [].slice.call(document.querySelectorAll(".aphorism[id]"));
    if (!index || !marks || marks.length < 2 || !window.IntersectionObserver) return;

    var links = {};
    [].forEach.call(index.querySelectorAll('a[href^="#a"]'), function (a) {
      links[a.getAttribute("href").slice(1)] = a;
    });
    if (!Object.keys(links).length) return;

    var shown = {}, current = null;
    function settle() {
      var first = null;
      marks.forEach(function (m) {
        if (shown[m.id] && (!first || m.offsetTop < first.offsetTop)) first = m;
      });
      var want = first ? links[first.id] : null;
      if (want === current) return;
      if (current) current.removeAttribute("aria-current");
      current = want;
      if (!current) return;
      current.setAttribute("aria-current", "true");
      /* Only when it has gone out of the column's own view; scrolling the
         index on every aphorism would fight the reader's own scrolling. */
      var box = index.getBoundingClientRect(), at = current.getBoundingClientRect();
      if (at.top < box.top || at.bottom > box.bottom) {
        current.scrollIntoView({ block: "nearest" });
      }
    }

    var watcher = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) { shown[entry.target.id] = entry.isIntersecting; });
      settle();
    }, { rootMargin: "-20% 0px -70% 0px" });
    marks.forEach(function (m) { watcher.observe(m); });
  })();

  /* ---- where you left off -------------------------------------------------
     The position of a recording was already being kept, under the page's own
     address, so that "Resume at 12:34" could appear on the sitting itself.
     What it could not do was tell you, from the front page, which sittings
     those were — an address is not a title.

     So alongside it a short list is kept: the last few sittings played, each
     with what it is, when it was given and where you stopped. Entirely in
     this browser. There is no account, nothing is sent anywhere, and clearing
     the browser clears it. The archive cannot see it and neither can I. */
  var RECENT = "avlokan:recent";
  var KEEP = 8;

  function recent() {
    try {
      var got = JSON.parse(localStorage.getItem(RECENT) || "[]");
      return Object.prototype.toString.call(got) === "[object Array]" ? got : [];
    } catch (err) { return []; }
  }

  function clock(seconds) {
    var s = Math.max(0, Math.floor(seconds));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    var mm = (h && m < 10 ? "0" : "") + m;
    return (h ? h + ":" : "") + mm + ":" + (r < 10 ? "0" : "") + r;
  }

  /* Under half a minute is not a sitting you were listening to, it is one you
     opened and thought better of. Offering to resume those would bury the
     ones you meant. */
  var WORTH_KEEPING = 30;

  function remember(seconds) {
    var art = document.querySelector("article.discourse[data-slug]");
    if (!art || !(seconds > WORTH_KEEPING)) return;
    var kept = recent().filter(function (x) { return x && x.slug !== art.dataset.slug; });
    kept.unshift({ slug: art.dataset.slug, text: art.dataset.text,
                   when: art.dataset.when, ref: art.dataset.ref,
                   t: Math.floor(seconds), at: Date.now() });
    try { localStorage.setItem(RECENT, JSON.stringify(kept.slice(0, KEEP))); } catch (err) {}
  }

  /* Someone who has been using the archive already has positions saved but no
     list, because the list did not exist when they listened. The page knows
     what it is, so the first visit back to a sitting puts it in. */
  (function () {
    var art = document.querySelector("article.discourse[data-slug]");
    if (!art) return;
    var was = 0;
    try { was = parseInt(localStorage.getItem("avlokan:pos:" + location.pathname) || "0", 10) || 0; }
    catch (err) { return; }
    var listed = recent().some(function (x) { return x && x.slug === art.dataset.slug; });
    if (was > WORTH_KEEPING && !listed) remember(was);
  })();

  /* Arriving from the front page: start the recording where it was left.
     Deliberately not the `#t…` that a search result uses — that one lands on
     a line to read, and should not begin playing under you.

     Called again after every in-page navigation. Following a link does not
     reload the document, it swaps what is in <main>, so anything that only
     runs when the script first loads never runs again. */
  function resumeFromHash() {
    var at = /^#at([0-9]+)$/.exec(location.hash || "");
    if (!at || !holder) return;
    if (playingId === holder.getAttribute("data-youtube")) return;
    play(true, parseInt(at[1], 10));
  }
  resumeFromHash();

  (function () {
    var panel = document.getElementById("recent");
    if (!panel) return;
    var items = recent().filter(function (x) { return x && x.slug && x.t; });
    if (!items.length) return;
    var list = panel.querySelector("ol");
    items.forEach(function (x) {
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = "d/" + x.slug + ".html#at" + x.t;
      a.appendChild(document.createElement("strong")).textContent = x.text || x.slug;
      var sub = document.createElement("span");
      sub.className = "muted";
      sub.textContent = [x.when, x.ref].filter(Boolean).join(" · ");
      a.appendChild(sub);
      var at = document.createElement("span");
      at.className = "at";
      at.textContent = "stopped at " + clock(x.t);
      a.appendChild(at);
      li.appendChild(a);
      list.appendChild(li);
    });
    panel.hidden = false;
    panel.querySelector(".forget").addEventListener("click", function () {
      try { localStorage.removeItem(RECENT); } catch (err) {}
      panel.hidden = true;
    });
  })();

  /* ---- corrections -------------------------------------------------------
     The form is on every discourse page and hidden on all of them. Shift+E
     opens it; so does `?edit` on the address, which is the only way in from a
     phone or a television.

     Saving tries the local editor first. `./edit.sh` answers POST /correction
     and writes the file; anywhere else that request fails and the same JSON
     goes to the clipboard instead, to be pasted wherever it can be acted on.
     Either way nothing is lost between noticing and recording. */
  var fix = document.querySelector("form.fix");
  if (fix) {
    var said = fix.querySelector(".said");

    function show() {
      fix.hidden = false;
      fix.scrollIntoView({ block: "center" });
      var first = fix.querySelector("input, textarea");
      if (first) first.focus();
    }

    /* `\b` doubled on purpose: these scripts live inside ordinary Python
       strings, where a single backslash-b is a backspace character. It
       compiled to /[?&]edit<BS>/, which matches nothing. */
    if (/[?&]edit\b/.test(location.search)) show();

    document.addEventListener("keydown", function (ev) {
      var on = ev.target && ev.target.tagName || "";
      if (/INPUT|TEXTAREA|SELECT/.test(on)) return;
      if (ev.shiftKey && (ev.key === "E" || ev.key === "e")) { ev.preventDefault(); show(); }
    });

    fix.querySelector(".cancel").addEventListener("click", function () {
      fix.hidden = true;
    });

    function tell(message) { said.textContent = message; }

    fix.addEventListener("submit", function (ev) {
      ev.preventDefault();
      /* Only what actually differs. A correction that restates the current
         value is noise in the history and, worse, reads later as a decision
         somebody made on purpose. */
      var body = { key: fix.dataset.key, was: fix.dataset.slug,
                   date: fix.dataset.date };
      var any = false;
      ["scripture", "reference", "part_no"].forEach(function (name) {
        var field = fix.elements[name];
        if (!field) return;
        var now = field.value.trim();
        if (now === field.defaultValue.trim()) return;
        body[name] = (name === "part_no")
          ? (now === "" ? null : parseInt(now, 10)) : now;
        any = true;
      });
      body.why = fix.elements.why.value.trim();
      if (!any) { tell("Nothing is different from what the page already says."); return; }
      if (!body.why) { tell("Say why, so a later reader can disagree with it."); return; }

      tell("Saving…");
      fetch("/correction", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }).then(function (r) {
        if (!r.ok) throw new Error("editor said " + r.status);
        return r.json();
      }).then(function (answer) {
        tell(answer.message || "Saved. Rebuilding…");
        if (answer.reload) setTimeout(function () { location.reload(); }, 900);
      }).catch(function () {
        var text = JSON.stringify(body, null, 1);
        var copy = navigator.clipboard && navigator.clipboard.writeText(text);
        if (copy) {
          copy.then(function () {
            tell("The editor is not running, so this is on your clipboard instead.");
          }).catch(function () { tell(text); });
        } else {
          tell(text);
        }
      });
    });
  }

  if (!blocks.length) { bind(); return; }

  /* ---- 2. click a timestamp to jump there ---- */
  function seek(seconds) {
    if (player && player.seekTo) { player.seekTo(seconds, true); player.playVideo(); }
    else if (holder) {
      var i = holder.querySelector("iframe");
      if (i) i.src = i.src.replace(/([?&])start=\d+/, "$1") + "&start=" + Math.floor(seconds) + "&autoplay=1";
    }
  }
  function wireTimestamps() {
    blocks.forEach(function (b) {
      var t = b.querySelector(".t");
      if (!t || t.dataset.wired) return;
      t.dataset.wired = "1";
      t.setAttribute("role", "button");
      t.setAttribute("tabindex", "0");
      t.addEventListener("click", function () { seek(parseFloat(b.dataset.start)); });
      t.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); t.click(); }
      });
    });
  }
  wireTimestamps();

  /* ---- 3. follow along, and remember where you stopped ---- */
  var follow = true, live = null;

  function highlight(seconds) {
    var found = null;
    for (var i = 0; i < blocks.length; i++) {
      if (seconds >= parseFloat(blocks[i].dataset.start)) found = blocks[i]; else break;
    }
    if (found === live) return;
    if (live) live.classList.remove("is-live");
    live = found;
    if (live) {
      live.classList.add("is-live");
      if (follow) live.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  function watch() {
    clearInterval(timer);
    var ticks = 0;
    timer = setInterval(function () {
      if (!player || !player.getCurrentTime) return;
      var t = player.getCurrentTime();
      highlight(t);
      try { localStorage.setItem(KEY, String(Math.floor(t))); } catch (err) {}
      /* The front-page list is rewritten whole each time, so it is kept to
         once every few seconds rather than every tick. */
      if (++ticks % 5 === 0) remember(t);
    }, 1000);
  }

  function toolbar() {
    var was = document.querySelector(".transcript .toolbar");
    if (was) was.remove();
    var bar = document.createElement("div");
    bar.className = "toolbar";
    var saved = 0;
    try { saved = parseInt(localStorage.getItem(KEY) || "0", 10) || 0; } catch (err) {}

    if (saved > 30) {
      var resume = document.createElement("button");
      var m = Math.floor(saved / 60), s = saved % 60;
      resume.textContent = "Resume at " + m + ":" + (s < 10 ? "0" : "") + s;
      resume.addEventListener("click", function () { seek(saved); });
      bar.appendChild(resume);
    }

    var f = document.createElement("button");
    f.textContent = "Follow along";
    f.setAttribute("aria-pressed", "true");
    f.addEventListener("click", function () {
      follow = !follow;
      f.setAttribute("aria-pressed", follow ? "true" : "false");
    });
    bar.appendChild(f);

    var copy = document.createElement("button");
    copy.textContent = "Copy my highlights";
    copy.addEventListener("click", function () {
      var picked = blocks.filter(function (b) { return b.classList.contains("is-marked"); });
      if (!picked.length) { copy.textContent = "Nothing highlighted yet"; setTimeout(function(){ copy.textContent = "Copy my highlights"; }, 1800); return; }
      var out = picked.map(function (b) {
        return "[" + b.querySelector(".t").textContent + "] " + b.querySelector(".w").innerText;
      }).join("\n\n") + "\n\n" + location.href;
      var ta = document.createElement("textarea");
      ta.value = out; ta.setAttribute("readonly", "");
      ta.style.cssText = "position:fixed;left:-9999px";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch (err) {}
      ta.remove();
      copy.textContent = "Copied " + picked.length + " passages";
      setTimeout(function () { copy.textContent = "Copy my highlights"; }, 2200);
    });
    bar.appendChild(copy);

    var section = document.getElementById("transcript");
    if (section) section.insertBefore(bar, section.querySelector(".blocks"));
  }

  /* ---- 4. highlight a passage by clicking its text ---- */
  function restoreMarks() {
    var marked = {};
    try { marked = JSON.parse(localStorage.getItem(MARKS) || "{}"); } catch (err) {}
    blocks.forEach(function (b) {
      if (marked[b.dataset.start]) b.classList.add("is-marked");
      var w = b.querySelector(".w");
      if (!w || w.dataset.wired) return;
      w.dataset.wired = "1";
      w.addEventListener("dblclick", function () {
        b.classList.toggle("is-marked");
        if (b.classList.contains("is-marked")) marked[b.dataset.start] = 1;
        else delete marked[b.dataset.start];
        try { localStorage.setItem(MARKS, JSON.stringify(marked)); } catch (err) {}
      });
    });
  }

  bindTranscript();

  /* The player itself is built when play is pressed; see section 1. */
})();
