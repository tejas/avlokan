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
    timer = setInterval(function () {
      if (!player || !player.getCurrentTime) return;
      var t = player.getCurrentTime();
      highlight(t);
      try { localStorage.setItem(KEY, String(Math.floor(t))); } catch (err) {}
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
