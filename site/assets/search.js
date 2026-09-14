/* Search, entirely in the browser. No server, no index service, nothing to
   keep paid for — the whole thing is two JSON files and this. */
(function () {
  "use strict";
  var q = document.getElementById("q");
  var deep = document.getElementById("deep");
  var out = document.getElementById("results");
  var count = document.getElementById("count");
  var data = null, words = null, wordsAsked = false, timer = null;

  function fold(t) {
    return (t || "").toLowerCase()
      .replace(/[̀-ͯ]/g, "")
      .replace(/\s+/g, " ").trim();
  }

  function terms(s) {
    return fold(s).split(" ").filter(Boolean);
  }

  function hit(hay, parts) {
    for (var i = 0; i < parts.length; i++) {
      if (hay.indexOf(parts[i]) < 0) return false;
    }
    return true;
  }

  function esc(t) {
    var d = document.createElement("div");
    d.textContent = t == null ? "" : t;
    return d.innerHTML;
  }

  /* Show where the match is, not the first 200 characters of something else. */
  function around(text, parts) {
    var low = fold(text), at = -1;
    for (var i = 0; i < parts.length && at < 0; i++) at = low.indexOf(parts[i]);
    if (at < 0) at = 0;
    var from = Math.max(0, at - 70), to = Math.min(text.length, at + 170);
    return (from ? "…" : "") + esc(text.slice(from, to)) + (to < text.length ? "…" : "");
  }

  function stamp(sec) {
    var m = Math.floor(sec / 60), s = Math.floor(sec % 60);
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  function run() {
    var raw = q.value.trim();
    if (!data || raw.length < 2) {
      out.innerHTML = "";
      count.textContent = raw && raw.length < 2 ? "Keep typing…" : "";
      return;
    }
    var parts = terms(raw), rows = [], n = 0;

    data.sittings.forEach(function (s) {
      if (n >= 300) return;
      var hay = fold([s.d, s.t, s.r, s.n].join(" "));
      if (!hit(hay, parts)) return;
      n++;
      rows.push('<li><a href="d/' + s.u + '.html">' +
        esc(s.r || s.t) + ' <span class="muted">' + esc(s.d) + '</span></a>' +
        '<div class="muted">' + esc(s.n || s.t) +
        (s.x ? ' &middot; transcript' : '') + '</div></li>');
    });

    var found = n;
    data.book.forEach(function (b) {
      if (n >= 300) return;
      if (!hit(fold(b.g + " " + b.e + " aphorism " + b.n), parts)) return;
      n++;
      rows.push('<li><a href="book/' + b.n + '.html">Aphorism ' + b.n + '</a>' +
        '<div class="guj" lang="gu">' + around(b.g, parts) + '</div></li>');
    });

    if (deep.checked && words) {
      words.forEach(function (w) {
        w.b.forEach(function (blk) {
          if (n >= 300) return;
          if (!hit(fold(blk[1]), parts)) return;
          n++;
          rows.push('<li><a href="d/' + w.u + '.html#t' + blk[0] + '">' +
            esc(w.t) + ' <span class="muted">' + esc(w.d) + ' &middot; ' +
            stamp(blk[0]) + '</span></a>' +
            '<div>' + around(blk[1], parts) + '</div></li>');
        });
      });
    }

    count.textContent = n ? (n >= 300 ? "First 300 matches" : n + " found") : "Nothing found";
    out.innerHTML = rows.length ? '<ul class="results">' + rows.join("") + "</ul>" : "";
  }

  function later() { clearTimeout(timer); timer = setTimeout(run, 120); }

  fetch("assets/search.json").then(function (r) { return r.json(); })
    .then(function (d) { data = d; run(); })
    .catch(function () { count.textContent = "The search index did not load."; });

  deep.addEventListener("change", function () {
    if (!deep.checked || wordsAsked) { run(); return; }
    wordsAsked = true;
    count.textContent = "Fetching the transcripts…";
    fetch("assets/transcripts.json").then(function (r) { return r.json(); })
      .then(function (d) { words = d; run(); })
      .catch(function () { count.textContent = "The transcripts did not load."; });
  });

  q.addEventListener("input", later);
})();
