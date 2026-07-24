/* Sound FX — queued browser player, Run-page panel + sound manager.
 *
 * Player: every page this file is loaded on listens for `sfx_speak` socket
 * messages. A message is one PHRASE — an ordered list of sound files (e.g.
 * pilot name + "lap" + number) played back to back. Phrases queue up and
 * play one after another; `priority` phrases (countdown marks) jump the
 * queue and interrupt the current one. Browser autoplay policy: the very
 * first sound may need one click/tap anywhere on the page — after that
 * everything plays automatically.
 *
 * Panel: on the Run page a compact panel (shared #rh-plugin-dock, next to
 * gate calibration / auto marshalling) shows a status lamp (green = the
 * plugin voices events), a mini player for the phrase being spoken (label,
 * progress bar, play/pause/stop), a playback-speed slider, quick
 * announcement buttons and a live feed of queued phrases.
 *
 * Manager: on the Settings page (inside the "Sound FX" panel) it renders
 * upload tables for event sounds, per-pilot sounds, announcements &
 * countdown pieces, number sounds and channel callouts.
 *
 * Standalone: /sound_fx/player serves a bare page with an #arm button that
 * turns any device into a dedicated speaker. */
(function () {
	'use strict';

	if (window.__rhSoundFx) { return; }
	window.__rhSoundFx = true;
	if (typeof io === 'undefined') { return; }

	var socket = io.connect(location.protocol + '//' + document.domain + ':' + location.port);
	var state = null;
	var unlocked = false;
	var standalone = !!document.getElementById('arm');

	// ------------------------------------------------------------ the queue

	var queue = [];        // pending phrases
	var current = null;    // {item, idx, audios, durs}
	var paused = false;
	var rate = 1.0;
	var ticker = null;

	function phraseTotal(cur) {
		var total = 0;
		for (var i = 0; i < cur.durs.length; i++) {
			if (!isFinite(cur.durs[i]) || !cur.durs[i]) { return null; }
			total += cur.durs[i];
		}
		return total;
	}

	function phraseElapsed(cur) {
		var t = 0;
		for (var i = 0; i < cur.idx; i++) { t += cur.durs[i] || 0; }
		var a = cur.audios[cur.idx];
		return t + (a ? a.currentTime : 0);
	}

	function startPhrase(item) {
		var cur = {
			item: item, idx: 0,
			audios: item.parts.map(function (p) { return new Audio(p.url); }),
			durs: item.parts.map(function () { return NaN; })
		};
		cur.audios.forEach(function (a, i) {
			a.preload = 'auto';
			a.addEventListener('loadedmetadata', function () {
				cur.durs[i] = a.duration;
			});
		});
		current = cur;
		playPart();
		renderPanel();
	}

	function playPart() {
		var cur = current;
		if (!cur) { return; }
		if (cur.idx >= cur.audios.length) { finishPhrase(); return; }
		var a = cur.audios[cur.idx];
		a.volume = (typeof cur.item.volume === 'number') ?
			Math.max(0, Math.min(1, cur.item.volume)) : 1;
		a.playbackRate = rate;
		a.onended = function () {
			if (current !== cur) { return; }
			cur.idx += 1;
			playPart();
		};
		a.onerror = a.onended;   // a broken file must not stall the queue
		var p = a.play();
		if (p && p.catch) {
			p.then(function () { unlocked = true; noteArmed(true); renderPanel(); })
			 .catch(function () { noteArmed(false); renderPanel(); });
		}
	}

	function finishPhrase() {
		current = null;
		kick();
	}

	function stopCurrent() {
		if (!current) { return; }
		var a = current.audios[current.idx];
		if (a) { a.onended = null; a.onerror = null; a.pause(); }
		current = null;
	}

	function kick() {
		if (!current && !paused && queue.length) {
			startPhrase(queue.shift());
		}
		renderPanel();
	}

	function enqueue(msg) {
		if (!msg || !msg.parts || !msg.parts.length) { return; }
		if (msg.priority) {
			// countdown marks are time-critical: interrupt a non-priority
			// phrase and speak right away
			var insertAt = 0;
			while (insertAt < queue.length && queue[insertAt].priority) { insertAt++; }
			queue.splice(insertAt, 0, msg);
			if (current && !current.item.priority) { stopCurrent(); }
		} else {
			queue.push(msg);
		}
		logLine('♫ ' + (msg.label || ''));
		kick();
	}

	function doPause() {
		paused = true;
		if (current) {
			var a = current.audios[current.idx];
			if (a) { a.pause(); }
		}
		renderPanel();
	}

	function doResume() {
		paused = false;
		if (current) {
			var a = current.audios[current.idx];
			if (a) {
				a.playbackRate = rate;
				var p = a.play();
				if (p && p.catch) { p.catch(function () { noteArmed(false); }); }
			}
		} else { kick(); }
		renderPanel();
	}

	function doStop() {
		queue = [];
		stopCurrent();
		paused = false;
		renderPanel();
	}

	function applyRate(r) {
		rate = Math.max(0.5, Math.min(2, r || 1));
		if (current) {
			var a = current.audios[current.idx];
			if (a) { a.playbackRate = rate; }
		}
		renderPanel();
	}

	function unlock() {
		if (unlocked) { return; }
		unlocked = true;
		noteArmed(true);
		if (current) {
			var a = current.audios[current.idx];
			if (a && a.paused && !paused) {
				var p = a.play();
				if (p && p.catch) { p.catch(function () {}); }
			}
		} else { kick(); }
		renderPanel();
	}
	['pointerdown', 'keydown', 'touchstart'].forEach(function (evt) {
		document.addEventListener(evt, unlock, { once: true, capture: true, passive: true });
	});

	// ------------------------------------------------------------- socket io

	socket.on('sfx_speak', function (msg) { enqueue(msg); });
	socket.on('sfx_play', function (msg) {   // legacy single-file message
		if (msg && msg.url) {
			enqueue({ label: msg.file || '', volume: msg.volume,
				parts: [{ url: msg.url, file: msg.file }] });
		}
	});
	socket.on('sfx_ctl', function (msg) {
		if (!msg) { return; }
		if (msg.action === 'pause') { doPause(); }
		else if (msg.action === 'resume') { doResume(); }
		else if (msg.action === 'stop') { doStop(); }
		else if (msg.action === 'rate') { applyRate(msg.rate); }
	});
	socket.on('connect', function () {
		socket.emit('sfx_get_state', {});
	});
	socket.on('sfx_state', function (s) {
		state = s || null;
		if (state && typeof state.rate === 'number') { applyRate(state.rate); }
		renderManager();
		renderPanel();
	});

	// -------------------------------------------------- standalone page bits

	function noteArmed(ok) {
		var btn = document.getElementById('arm');
		if (!btn) { return; }
		if (ok) {
			btn.classList.add('armed');
			btn.textContent = 'Audio enabled ✓';
		} else {
			btn.classList.remove('armed');
			btn.textContent = 'Tap to enable audio';
		}
	}
	function logLine(text) {
		var log = document.getElementById('log');
		if (!log) { return; }
		var when = new Date().toTimeString().slice(0, 8);
		log.textContent = (when + '  ' + text + '\n' + log.textContent)
			.split('\n').slice(0, 6).join('\n');
	}
	if (standalone) {
		document.getElementById('arm').addEventListener('click', function () {
			// play a short silent buffer to satisfy the autoplay policy
			try {
				var ctx = new (window.AudioContext || window.webkitAudioContext)();
				ctx.resume && ctx.resume();
				var osc = ctx.createOscillator();
				var gain = ctx.createGain();
				gain.gain.value = 0;
				osc.connect(gain); gain.connect(ctx.destination);
				osc.start(); osc.stop(ctx.currentTime + 0.05);
			} catch (e) { /* AudioContext unavailable — gesture alone helps */ }
			unlock();
			logLine('player armed');
		});
	}

	// ----------------------------------------------------------------- utils

	function ensureCss() {
		if (document.getElementById('sfx-css')) { return; }
		var l = document.createElement('link');
		l.id = 'sfx-css'; l.rel = 'stylesheet';
		l.href = '/sound_fx/static/sound_fx.css';
		(document.head || document.documentElement).appendChild(l);
	}

	function el(tag, cls, text) {
		var e = document.createElement(tag);
		if (cls) { e.className = cls; }
		if (text != null) { e.textContent = text; }
		return e;
	}

	// -------------------------------------------------------- Run-page panel

	var panel = null, userOpen = null, autoOpen = false;
	var lightMq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;

	function isLight() {
		var theme = (state && state.theme) || 'dark';
		if (theme === 'light') { return true; }
		if (theme === 'auto') { return !!(lightMq && lightMq.matches); }
		return false;
	}
	function onRunPage() { return !!document.getElementById('leaderboard'); }

	// shared dock for our plugin panels above the pilot table (created by
	// whichever of our plugins loads first — same helper ships in each)
	function dock() {
		var d = document.getElementById('rh-plugin-dock');
		if (d) { return d; }
		var anchor = document.getElementById('leaderboard');
		if (!anchor || !anchor.parentNode) { return null; }
		d = el('div', 'rh-plugin-dock');
		d.id = 'rh-plugin-dock';
		anchor.parentNode.insertBefore(d, anchor);
		return d;
	}
	function place() {
		if (!panel) { return; }
		var d = dock();
		if (d && panel.parentNode !== d) { d.appendChild(panel); }
	}

	function panelOpen() {
		if (userOpen !== null) { return userOpen; }
		return autoOpen;
	}

	function ensurePanel() {
		if (panel) { place(); return panel; }
		panel = el('div', 'rh-sfx'); panel.id = 'rh-sfx';
		panel.innerHTML =
			'<div class="rh-sfx-head">' +
			'<span class="rh-sfx-chev">▸</span>' +
			'<span class="rh-sfx-lamp" title=""></span>' +
			'<div class="rh-sfx-title"><span class="rh-sfx-spark">♫</span> Sound FX</div>' +
			'<div class="rh-sfx-headsum"></div></div>' +
			'<div class="rh-sfx-body">' +
			'<div class="rh-sfx-player">' +
			'<button class="rh-sfx-btn rh-sfx-bplay" title="Play / resume">⏵</button>' +
			'<button class="rh-sfx-btn rh-sfx-bpause" title="Pause">⏸</button>' +
			'<button class="rh-sfx-btn rh-sfx-bstop" title="Stop and clear the queue">⏹</button>' +
			'<span class="rh-sfx-now"></span>' +
			'<span class="rh-sfx-time"></span></div>' +
			'<div class="rh-sfx-track"><div class="rh-sfx-bar"></div></div>' +
			'<div class="rh-sfx-ctl2">' +
			'<span class="rh-sfx-rlbl" title="Playback speed">speed</span>' +
			'<input class="rh-sfx-rate" type="range" min="50" max="200" step="5">' +
			'<span class="rh-sfx-rval"></span>' +
			'<span class="rh-sfx-flex"></span>' +
			'<button class="rh-sfx-btn rh-sfx-bann" data-key="arm" title="Play the ‘arm your quads’ announcement">📢 arm</button>' +
			'<button class="rh-sfx-btn rh-sfx-bann" data-key="next_group" title="Announce the current heat: each pilot + channel">📢 group</button>' +
			'<button class="rh-sfx-btn rh-sfx-benable"></button></div>' +
			'<div class="rh-sfx-feed"></div></div>';

		panel.querySelector('.rh-sfx-head').addEventListener('click', function (e) {
			if (e.target.closest('button, input')) { return; }
			userOpen = !panelOpen();
			renderPanel();
		});
		panel.querySelector('.rh-sfx-bplay').addEventListener('click', function () {
			socket.emit('sfx_ctl', { action: 'resume' });
		});
		panel.querySelector('.rh-sfx-bpause').addEventListener('click', function () {
			socket.emit('sfx_ctl', { action: 'pause' });
		});
		panel.querySelector('.rh-sfx-bstop').addEventListener('click', function () {
			socket.emit('sfx_ctl', { action: 'stop' });
		});
		var slider = panel.querySelector('.rh-sfx-rate');
		slider.addEventListener('input', function () {
			panel.querySelector('.rh-sfx-rval').textContent =
				(slider.value / 100).toFixed(2) + '×';
		});
		slider.addEventListener('change', function () {
			socket.emit('sfx_ctl', { action: 'rate', rate: parseInt(slider.value, 10) });
		});
		panel.querySelectorAll('.rh-sfx-bann').forEach(function (b) {
			b.addEventListener('click', function () {
				socket.emit('sfx_announce', { key: b.getAttribute('data-key') });
			});
		});
		panel.querySelector('.rh-sfx-benable').addEventListener('click', function () {
			socket.emit('sfx_set_enabled', { enabled: !(state && state.enabled) });
		});
		place();
		return panel;
	}

	function tickProgress() {
		if (!panel) { return; }
		var bar = panel.querySelector('.rh-sfx-bar');
		var time = panel.querySelector('.rh-sfx-time');
		if (!current) {
			bar.style.width = '0';
			time.textContent = '';
			return;
		}
		var total = phraseTotal(current);
		var w;
		if (total) {
			var elapsed = phraseElapsed(current);
			w = Math.max(0, Math.min(100, elapsed / total * 100));
			time.textContent = elapsed.toFixed(1) + ' / ' + total.toFixed(1) + 's';
		} else {
			// durations still loading — approximate by part position
			var n = current.audios.length;
			var a = current.audios[current.idx];
			var frac = (a && isFinite(a.duration) && a.duration) ?
				a.currentTime / a.duration : 0;
			w = Math.min(100, ((current.idx + frac) / n) * 100);
			time.textContent = '';
		}
		bar.style.width = w + '%';
	}
	function startTicker() {
		if (!ticker) { ticker = setInterval(tickProgress, 150); }
	}
	function stopTicker() {
		if (ticker) { clearInterval(ticker); ticker = null; }
	}

	function hasAnn(key) {
		if (!state || !state.announces) { return false; }
		for (var i = 0; i < state.announces.length; i++) {
			if (state.announces[i].key === key) { return !!state.announces[i].file; }
		}
		return false;
	}

	function renderPanel() {
		if (!onRunPage()) { return; }
		if (!state) { return; }
		ensureCss();
		ensurePanel();

		// something to say → open the panel for this page session
		if (current || queue.length) { autoOpen = true; }

		var open = panelOpen();
		panel.className = 'rh-sfx' + (open ? '' : ' rh-sfx-collapsed') +
			(isLight() ? ' rh-sfx-light' : '');
		panel.querySelector('.rh-sfx-chev').textContent = open ? '▾' : '▸';

		// the lamp: green — the plugin voices events; amber — enabled but this
		// browser still needs one click before it may play audio; gray — off
		var lamp = panel.querySelector('.rh-sfx-lamp');
		var lampCls = 'rh-sfx-lamp ';
		if (!state.enabled) {
			lampCls += 'rh-sfx-lamp-off';
			lamp.title = 'Sound FX is OFF — RotorHazard default sounds only';
		} else if (!unlocked) {
			lampCls += 'rh-sfx-lamp-locked';
			lamp.title = 'Sound FX is on, but this browser needs one ' +
				'click/tap anywhere on the page before it may play audio';
		} else {
			lampCls += 'rh-sfx-lamp-on';
			lamp.title = 'Sound FX voices race events (instead of the ' +
				'standard sounds — mute RH voice callouts in Audio Control)';
		}
		lamp.className = lampCls;

		// header summary
		var sum = panel.querySelector('.rh-sfx-headsum');
		if (current) {
			sum.innerHTML = '<span class="rh-sfx-sumnow">' +
				esc(current.item.label || '…') + '</span>' +
				(queue.length ? '<span class="rh-sfx-summut"> +' + queue.length + '</span>' : '');
		} else if (queue.length) {
			sum.innerHTML = '<span class="rh-sfx-summut">' + queue.length +
				' queued' + (paused ? ' — paused' : '') + '</span>';
		} else {
			sum.innerHTML = '<span class="rh-sfx-summut">' +
				(state.enabled ? (paused ? 'paused' : 'idle') : 'off') + '</span>';
		}

		if (!open) { stopTicker(); return; }

		// mini player
		var now = panel.querySelector('.rh-sfx-now');
		if (current) {
			now.textContent = current.item.label || '…';
			now.classList.remove('rh-sfx-idle');
		} else {
			now.textContent = paused ? 'paused' :
				(queue.length ? 'waiting…' : 'nothing queued');
			now.classList.add('rh-sfx-idle');
		}
		panel.querySelector('.rh-sfx-bplay').classList.toggle('rh-sfx-active', !paused);
		panel.querySelector('.rh-sfx-bpause').classList.toggle('rh-sfx-active', paused);

		// speed slider (do not fight the user mid-drag)
		var slider = panel.querySelector('.rh-sfx-rate');
		if (document.activeElement !== slider) {
			slider.value = Math.round(rate * 100);
		}
		panel.querySelector('.rh-sfx-rval').textContent = rate.toFixed(2) + '×';

		// announce buttons: disabled when no sound file is uploaded
		panel.querySelectorAll('.rh-sfx-bann').forEach(function (b) {
			var key = b.getAttribute('data-key');
			var ok = key === 'next_group' ?
				(hasAnn('next_group') || (state.pilots || []).some(function (p) { return p.file; })) :
				hasAnn(key);
			b.disabled = !ok;
			if (!ok) { b.title = 'Upload the sound in Settings → Sound FX first'; }
		});

		var en = panel.querySelector('.rh-sfx-benable');
		en.textContent = state.enabled ? 'SFX: ON' : 'SFX: OFF';
		en.className = 'rh-sfx-btn rh-sfx-benable ' +
			(state.enabled ? 'rh-sfx-en-on' : 'rh-sfx-en-off');

		// live feed: phrases waiting their turn (the spoken one is on the
		// player bar above; it disappears from here once it starts)
		var feed = panel.querySelector('.rh-sfx-feed');
		feed.innerHTML = '';
		queue.slice(0, 8).forEach(function (item) {
			var row = el('div', 'rh-sfx-qrow' + (item.priority ? ' rh-sfx-qprio' : ''));
			row.appendChild(el('span', 'rh-sfx-qdot', item.priority ? '⚡' : '•'));
			row.appendChild(el('span', 'rh-sfx-qlbl', item.label || ''));
			feed.appendChild(row);
		});
		if (queue.length > 8) {
			feed.appendChild(el('div', 'rh-sfx-qmore', '+' + (queue.length - 8) + ' more'));
		}

		if (current) { startTicker(); } else { stopTicker(); }
		tickProgress();
	}

	function esc(s) {
		return String(s).replace(/[&<>"]/g, function (c) {
			return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
		});
	}

	// -------------------------------------------------------------- manager

	function mountPoint() {
		var m = document.getElementById('sfx-manager');
		if (m) { return m; }
		var panelEl = document.querySelector('#ui-custom-sound_fx .panel-content');
		if (panelEl) {
			m = el('div'); m.id = 'sfx-manager';
			panelEl.appendChild(m);
			return m;
		}
		return null;
	}

	function hideLoaderPanels() {
		['run', 'marshal', 'format'].forEach(function (page) {
			var p = document.getElementById('ui-custom-sound_fx_load_' + page);
			if (p) { p.style.display = 'none'; }
		});
	}

	function uploadRow(kind, key, label, sub, item) {
		var row = el('div', 'sfx-row');
		var name = el('div', 'sfx-name');
		name.appendChild(el('span', 'sfx-label', label));
		if (sub) { name.appendChild(el('span', 'sfx-sub', sub)); }
		row.appendChild(name);

		row.appendChild(el('div', 'sfx-file' + (item.file ? '' : ' sfx-none'),
			item.file || '—'));

		var actions = el('div', 'sfx-actions');

		var upLabel = el('label', 'sfx-btn sfx-up', item.file ? 'Replace' : 'Upload');
		var input = document.createElement('input');
		input.type = 'file';
		input.accept = '.mp3,.wav,.ogg,.m4a,audio/*';
		input.style.display = 'none';
		input.addEventListener('change', function () {
			if (!input.files || !input.files[0]) { return; }
			var fd = new FormData();
			fd.append('kind', kind);
			fd.append('key', key);
			fd.append('file', input.files[0]);
			upLabel.textContent = '…';
			fetch('/sound_fx/upload', { method: 'POST', body: fd })
				.then(function (r) { return r.json(); })
				.then(function (r) {
					if (!r.ok) { alert('Sound FX upload failed: ' + (r.error || 'unknown')); }
					socket.emit('sfx_get_state', {});
				})
				.catch(function () {
					alert('Sound FX upload failed');
					socket.emit('sfx_get_state', {});
				});
		});
		upLabel.appendChild(input);
		actions.appendChild(upLabel);

		if (item.file) {
			var test = el('button', 'sfx-btn', '▶');
			test.title = 'Preview here';
			test.addEventListener('click', function () {
				enqueue({ label: item.file, volume: state ? state.volume : 1,
					parts: [{ url: item.url, file: item.file }] });
			});
			actions.appendChild(test);

			var bcast = el('button', 'sfx-btn', '📢');
			bcast.title = 'Play on every connected player';
			bcast.addEventListener('click', function () {
				socket.emit('sfx_test', { kind: kind, key: key });
			});
			actions.appendChild(bcast);

			var del = el('button', 'sfx-btn sfx-del', '✕');
			del.title = 'Remove sound';
			del.addEventListener('click', function () {
				if (confirm('Remove sound "' + item.file + '"?')) {
					socket.emit('sfx_delete', { kind: kind, key: key });
				}
			});
			actions.appendChild(del);
		}
		row.appendChild(actions);
		return row;
	}

	// extra number/channel rows added by hand this session (until uploaded)
	var extraNums = [], extraChans = [];

	function addKeyRow(box, placeholder, onAdd) {
		var row = el('div', 'sfx-row sfx-addrow');
		var input = document.createElement('input');
		input.type = 'text'; input.placeholder = placeholder;
		input.className = 'sfx-addkey';
		var btn = el('button', 'sfx-btn', 'Add row');
		btn.addEventListener('click', function () {
			if (onAdd(input.value.trim())) { input.value = ''; renderManager(); }
			else if (input.value.trim()) { alert('Bad key: ' + input.value); }
		});
		row.appendChild(input); row.appendChild(btn);
		box.appendChild(row);
	}

	function renderManager() {
		hideLoaderPanels();
		var mount = mountPoint();
		if (!mount || !state) { return; }
		ensureCss();
		mount.innerHTML = '';

		if (!state.enabled) {
			mount.appendChild(el('div', 'sfx-warn',
				'Sounds are disabled — turn on "Enable sounds" above.'));
		}

		mount.appendChild(el('h3', 'sfx-h', 'Event sounds'));
		var evBox = el('div', 'sfx-box');
		state.events.forEach(function (ev) {
			evBox.appendChild(uploadRow('event', ev.key, ev.label, null, ev));
		});
		mount.appendChild(evBox);

		mount.appendChild(el('h3', 'sfx-h', 'Pilot sounds'));
		mount.appendChild(el('div', 'sfx-hint',
			'Personal sound for each pilot (e.g. their name). Played on the ' +
			'triggers selected above, in the next-group announcement and ' +
			'before the lap number.'));
		var pBox = el('div', 'sfx-box');
		if (!state.pilots.length) {
			pBox.appendChild(el('div', 'sfx-hint', 'No pilots yet — add pilots on the Format page.'));
		}
		state.pilots.forEach(function (p) {
			pBox.appendChild(uploadRow('pilot', String(p.id),
				p.callsign || p.name || ('Pilot ' + p.id),
				p.name && p.name !== p.callsign ? p.name : null, p));
		});
		mount.appendChild(pBox);

		mount.appendChild(el('h3', 'sfx-h', 'Announcements & countdown pieces'));
		mount.appendChild(el('div', 'sfx-hint',
			'Building blocks: race-start / race-end countdown marks, the ' +
			'"next group" prefix, the word "lap", the "arm your quads" call.'));
		var aBox = el('div', 'sfx-box');
		(state.announces || []).forEach(function (a) {
			aBox.appendChild(uploadRow('ann', a.key, a.label, 'ann_' + a.key, a));
		});
		mount.appendChild(aBox);

		mount.appendChild(el('h3', 'sfx-h', 'Numbers'));
		mount.appendChild(el('div', 'sfx-hint',
			'Spoken numbers, used for lap counts ("<pilot> lap 3") and minute ' +
			'marks ("2 minutes to start"). Add more rows for bigger numbers.'));
		var nBox = el('div', 'sfx-box');
		var numItems = (state.numbers || []).slice();
		var have = {};
		numItems.forEach(function (n) { have[String(n.key)] = true; });
		extraNums.forEach(function (k) {
			if (!have[String(k)]) { numItems.push({ key: k, file: null, url: null }); }
		});
		numItems.sort(function (a, b) { return a.key - b.key; });
		numItems.forEach(function (n) {
			nBox.appendChild(uploadRow('num', String(n.key), String(n.key),
				'num_' + n.key, n));
		});
		addKeyRow(nBox, 'number, e.g. 12', function (v) {
			var n = parseInt(v, 10);
			if (isNaN(n) || n < 0 || n > 999) { return false; }
			if (extraNums.indexOf(n) < 0) { extraNums.push(n); }
			return true;
		});
		mount.appendChild(nBox);

		mount.appendChild(el('h3', 'sfx-h', 'Channel callouts'));
		mount.appendChild(el('div', 'sfx-hint',
			'Channel names (R1, F4, …) for the next-group announcement.' +
			((state.active_channels || []).length ?
				' Current frequency set uses: ' +
				state.active_channels.map(function (c) { return c.toUpperCase(); }).join(', ') + '.'
				: '')));
		var cBox = el('div', 'sfx-box');
		var chanItems = (state.channels || []).slice();
		var haveC = {};
		chanItems.forEach(function (c) { haveC[c.key] = true; });
		extraChans.forEach(function (k) {
			if (!haveC[k]) { chanItems.push({ key: k, file: null, url: null }); }
		});
		chanItems.sort(function (a, b) { return a.key < b.key ? -1 : 1; });
		var active = {};
		(state.active_channels || []).forEach(function (c) { active[c] = true; });
		chanItems.forEach(function (c) {
			cBox.appendChild(uploadRow('chan', c.key,
				c.key.toUpperCase() + (active[c.key] ? ' ●' : ''),
				'chan_' + c.key, c));
		});
		addKeyRow(cBox, 'channel, e.g. e4', function (v) {
			v = v.toLowerCase();
			if (!/^[a-z][0-9]{1,2}$/.test(v)) { return false; }
			if (extraChans.indexOf(v) < 0) { extraChans.push(v); }
			return true;
		});
		mount.appendChild(cBox);

		mount.appendChild(el('div', 'sfx-hint',
			'Sounds play in every open RotorHazard page. For a dedicated ' +
			'speaker device open /sound_fx/player on it.'));
	}

	// the settings page builds panels asynchronously — retry the mount briefly
	var tries = 0;
	var timer = setInterval(function () {
		tries += 1;
		hideLoaderPanels();
		if ((state && mountPoint() && (renderManager() || true)) || tries > 40) {
			clearInterval(timer);
		}
	}, 500);

	// the run page: keep trying to place the panel while the dock builds
	var ptries = 0;
	var ptimer = setInterval(function () {
		if (onRunPage() && state) { renderPanel(); }
		if (panel) { place(); }
		if (++ptries > 40) { clearInterval(ptimer); }
	}, 500);

	if (lightMq) {
		var onScheme = function () { renderPanel(); };
		if (lightMq.addEventListener) { lightMq.addEventListener('change', onScheme); }
		else if (lightMq.addListener) { lightMq.addListener(onScheme); }
	}
})();
