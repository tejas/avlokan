/* Asks YouTube to play every recording in a hidden player and notes the ones
   it refuses. Four at a time, because the point is to finish, not to be fast.
   Error 101 and 150 are "the owner or a rights holder disallowed embedding";
   2 is a malformed id and 5 a playback failure. */
(function () {
  "use strict";
  var LANES = 4;
  /* A full pass takes about ten minutes, which is long enough that the tab
     will sometimes be reloaded part way. Results are kept as they are found
     so a reload picks up where it stopped instead of starting again. */
  var KEY = "avlokan.embedcheck";
  var seen = {};
  try { seen = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { seen = {}; }
  function remember(id, code) {
    seen[id] = code;
    try { localStorage.setItem(KEY, JSON.stringify(seen)); } catch (e) {}
  }

  var next = 0, checked = 0, refused = 0, players = [];
  var stage = document.getElementById("stage");
  var bad = document.getElementById("bad");
  var count = document.getElementById("count");

  function report(item, code) {
    refused++;
    var li = document.createElement("li");
    li.innerHTML = '<a href="https://www.youtube.com/watch?v=' + item.id + '">' +
      item.id + '</a> &middot; ' + item.date + ' &middot; ' + item.text +
      '<br><span class="muted">' + (item.title || "") +
      ' &middot; error ' + code + ' &middot; <a href="d/' + item.page +
      '.html">page</a></span>';
    bad.appendChild(li);
  }

  function byText() {
    var rows = {};
    VIDEOS.forEach(function (v) {
      var code = seen[v.id];
      if (code === undefined) return;
      rows[v.text] = rows[v.text] || { bad: 0, all: 0 };
      rows[v.text].all++;
      if (code) rows[v.text].bad++;
    });
    var names = Object.keys(rows).sort(function (a, b) {
      return (rows[b].bad / rows[b].all) - (rows[a].bad / rows[a].all) ||
             rows[b].all - rows[a].all;
    });
    var out = "";
    names.forEach(function (n) {
      var r = rows[n];
      out += "<tr><td>" + n + "</td><td>" + r.bad + "</td><td>" + r.all +
             "</td><td>" + Math.round(r.bad / r.all * 100) + "%</td></tr>";
    });
    document.getElementById("bytext").innerHTML = out;
  }

  function tick() {
    count.textContent = checked + " of " + VIDEOS.length + " checked, " +
                        refused + " refused";
    if (checked % 20 === 0 || checked === VIDEOS.length) byText();
    if (checked === VIDEOS.length) {
      document.getElementById("done").hidden = false;
      document.getElementById("summary").textContent =
        refused + " of " + VIDEOS.length + " recordings cannot be embedded.";
    }
  }

  function lane(slot) {
    /* Skip anything a previous pass already settled. */
    while (next < VIDEOS.length && seen[VIDEOS[next].id] !== undefined) {
      next++;      /* already counted and listed by replay() above */
    }
    tick();
    if (next >= VIDEOS.length) return;
    var item = VIDEOS[next++];
    var mount = document.createElement("div");
    mount.id = "probe-" + slot + "-" + item.id;
    stage.appendChild(mount);
    var settled = false;
    function finish(code) {
      if (settled) return;
      settled = true;
      remember(item.id, code || 0);
      if (code) report(item, code);
      checked++; tick();
      try { players[slot].destroy(); } catch (e) {}
      mount.remove();
      lane(slot);
    }
    players[slot] = new YT.Player(mount.id, {
      videoId: item.id, height: 90, width: 160,
      playerVars: { autoplay: 0, rel: 0 },
      events: {
        /* A refused recording readies first and only then reports the
           refusal, so readiness on its own means nothing — it has to be
           given a moment to complain before being counted as fine. */
        onReady: function () { setTimeout(function () { finish(0); }, 3000); },
        onError: function (e) { finish(e.data); }
      }
    });
    /* A player that neither readies nor errors would stall the lane. */
    setTimeout(function () { finish(0); }, 20000);
  }

  /* Show whatever a previous pass already found, straight away. This used to
     wait for YouTube's API to load before drawing anything, so a run whose
     results were all in hand still showed an empty page if the API could not
     be reached. */
  (function replay() {
    VIDEOS.forEach(function (v) {
      var code = seen[v.id];
      if (code === undefined) return;
      checked++;
      if (code) report(v, code);
    });
    if (checked) { next = 0; tick(); byText(); }
  })();

  document.getElementById("copy").addEventListener("click", function () {
    var lines = VIDEOS.filter(function (v) { return seen[v.id]; })
      .map(function (v) {
        return v.id + "\t" + v.date + "\t" + v.text + "\t" + (v.title || "");
      });
    var box = document.createElement("textarea");
    box.value = lines.join("\n");
    document.body.appendChild(box);
    box.select();
    try { document.execCommand("copy"); } catch (e) {}
    box.remove();
    document.getElementById("copied").textContent = lines.length + " copied";
  });

  document.getElementById("again").addEventListener("click", function () {
    try { localStorage.removeItem(KEY); } catch (e) {}
    location.reload();
  });

  document.getElementById("go").addEventListener("click", function () {
    this.disabled = true;
    stage.hidden = false;
    stage.style.cssText = "position:fixed;left:-9999px;top:0";
    var tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(tag);
    window.onYouTubeIframeAPIReady = function () {
      for (var i = 0; i < LANES; i++) lane(i);
    };
  });
})();
