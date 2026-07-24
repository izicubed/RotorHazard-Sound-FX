/* Sound FX — browser player + sound manager.
 *
 * Player: every page this file is loaded on listens for `sfx_play` socket
 * messages and plays the given audio URL (browser autoplay policy: the very
 * first sound may need one click/tap anywhere on the page — after that
 * everything plays automatically; a pending sound is played right after the
 * unlocking gesture).
 *
 * Manager: on the Settings page (inside the "Sound FX" panel) it renders an
 * upload table for event sounds and per-pilot sounds, driven by the server
 * `sfx_state` snapshot.
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
	var pending = null;      // last sound that failed to autoplay
	var standalone = !!document.getElementById('arm');

	// ------------------------------------------------------------- playback

	function playUrl(url, volume) {
		var audio = new Audio(url);
		audio.volume = (typeof volume === 'number') ? Math.max(0, Math.min(1, volume)) : 1;
		var p = audio.play();
		if (p && p.catch) {
			p.then(function () { unlocked = true; noteArmed(true); })
			 .catch(function () {
				pending = { url: url, volume: volume };
				noteArmed(false);
			 });
		}
		return audio;
	}

	function unlock() {
		if (unlocked) { return; }
		unlocked = true;
		noteArmed(true);
		if (pending) {
			var s = pending; pending = null;
			playUrl(s.url, s.volume);
		}
	}
	['pointerdown', 'keydown', 'touchstart'].forEach(function (evt) {
		document.addEventListener(evt, unlock, { once: true, capture: true, passive: true });
	});

	socket.on('sfx_play', function (msg) {
		if (!msg || !msg.url) { return; }
		playUrl(msg.url, msg.volume);
		logLine('♫ ' + (msg.file || msg.url));
	});
	socket.on('connect', function () {
		socket.emit('sfx_get_state', {});
	});
	socket.on('sfx_state', function (s) {
		state = s || null;
		renderManager();
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

	// -------------------------------------------------------------- manager

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

	function mountPoint() {
		var m = document.getElementById('sfx-manager');
		if (m) { return m; }
		var panel = document.querySelector('#ui-custom-sound_fx .panel-content');
		if (panel) {
			m = el('div'); m.id = 'sfx-manager';
			panel.appendChild(m);
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
			test.addEventListener('click', function () { playUrl(item.url, state ? state.volume : 1); });
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

		var evHead = el('h3', 'sfx-h', 'Event sounds');
		mount.appendChild(evHead);
		var evBox = el('div', 'sfx-box');
		state.events.forEach(function (ev) {
			evBox.appendChild(uploadRow('event', ev.key, ev.label, null, ev));
		});
		mount.appendChild(evBox);

		mount.appendChild(el('h3', 'sfx-h', 'Pilot sounds'));
		mount.appendChild(el('div', 'sfx-hint',
			'Personal sound for each pilot (e.g. their name). Played on the ' +
			'triggers selected above: every lap, holeshot, finish, winner.'));
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
})();
