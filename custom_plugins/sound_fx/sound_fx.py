'''
Sound FX controller for RotorHazard.

Plays uploaded MP3/WAV/OGG files on race events and per-pilot sounds
(e.g. a pilot-name callout) when that pilot records a lap, scores the
holeshot, finishes or wins. Files are uploaded from a panel on the
Settings page, stored under the server data dir and streamed to every
connected browser over socket.io; an optional standalone player page
turns any device on the network into a dedicated speaker, and playback
on the server's own audio output (mpg123/ffplay/...) can be enabled too.
'''

import itertools
import json
import logging
import os
import re
import shutil
import subprocess
import time

import gevent

from flask import Blueprint, request, jsonify, send_from_directory, Response

from eventmanager import Evt
from RHUI import UIField, UIFieldType, UIFieldSelectOption

logger = logging.getLogger(__name__)

PLUGIN_ID = 'sound_fx'

# socket.io message names
EV_GET_STATE = 'sfx_get_state'
EV_DELETE = 'sfx_delete'
EV_TEST = 'sfx_test'
EV_CTL = 'sfx_ctl'                  # player control from any panel
EV_SET_ENABLED = 'sfx_set_enabled'
EV_ANNOUNCE = 'sfx_announce'        # manual announcement buttons
MSG_STATE = 'sfx_state'
MSG_PLAY = 'sfx_play'               # legacy single-file message (still sent)
MSG_SPEAK = 'sfx_speak'             # queued phrase: list of files + label
MSG_CTL = 'sfx_ctl'                 # rebroadcast control to every player

# options
OPT_ENABLED = 'sfx_enabled'
OPT_VOLUME = 'sfx_volume'
OPT_PILOT_ON_LAP = 'sfx_pilot_on_lap'
OPT_PILOT_ON_HOLESHOT = 'sfx_pilot_on_holeshot'
OPT_PILOT_ON_DONE = 'sfx_pilot_on_done'
OPT_PILOT_ON_WIN = 'sfx_pilot_on_win'
OPT_PILOT_REPLACES = 'sfx_pilot_replaces'
OPT_SERVER_PLAYBACK = 'sfx_server_playback'
OPT_RATE = 'sfx_rate'                    # playback rate, percent 50..200
OPT_LAP_NUMBER = 'sfx_lap_number'        # say lap number after pilot name
OPT_CD_START = 'sfx_cd_start'            # countdown to scheduled race start
OPT_CD_END = 'sfx_cd_end'                # countdown to end of a timed race
OPT_NEXT_GROUP = 'sfx_next_group'        # announce pilots+channels on heat set
OPT_THEME = 'sfx_theme'

ALLOWED_EXTS = ('.mp3', '.wav', '.ogg', '.m4a')
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Event catalogue: key (used in file names) -> (Evt name, label shown in UI).
# RACE_LAP_RECORDED is routed internally to 'holeshot' for lap 0, so the
# holeshot entry below has no Evt of its own.
EVENT_SOUNDS = [
    ('schedule',   Evt.RACE_SCHEDULE,   'Race scheduled'),
    ('stage',      Evt.RACE_STAGE,      'Race staging (arm)'),
    ('start',      Evt.RACE_START,      'Race start'),
    ('finish',     Evt.RACE_FINISH,     'Race finish (time expired)'),
    ('stop',       Evt.RACE_STOP,       'Race stop'),
    ('win',        Evt.RACE_WIN,        'Winner declared'),
    ('holeshot',   None,                'Holeshot (first pass, any pilot)'),
    ('lap',        Evt.RACE_LAP_RECORDED, 'Lap recorded (any pilot)'),
    ('pilot_done', Evt.RACE_PILOT_DONE, 'Pilot finished'),
    ('laps_save',  Evt.LAPS_SAVE,       'Race saved'),
    ('rounds_complete', Evt.ROUNDS_COMPLETE, 'Rounds complete'),
]

# which event keys may be voiced with the per-pilot sound, and the option
# that enables each of them
PILOT_TRIGGER_OPTS = {
    'lap': OPT_PILOT_ON_LAP,
    'holeshot': OPT_PILOT_ON_HOLESHOT,
    'pilot_done': OPT_PILOT_ON_DONE,
    'win': OPT_PILOT_ON_WIN,
}

# Announcement / countdown building blocks: key (file name `ann_<key>.<ext>`)
# -> label shown in the manager UI. Phrases are composed from these plus
# `num_<n>` number files and `chan_<band><ch>` channel files.
ANNOUNCE_SOUNDS = [
    ('arm',        'Arm your quads (hands-off / by readiness)'),
    ('next_group', 'Next group — prefix, then pilot + channel'),
    ('lap_word',   'Word "lap" (pilot name + lap + number)'),
    ('cd_minutes', 'Tail "…minutes to start" (after a number)'),
    ('cd_30',      '30 seconds to start'),
    ('cd_10',      '10 seconds to start'),
    ('cd_5',       '5 seconds to start'),
    ('end_minutes', 'Tail "…minutes remaining" (after a number)'),
    ('end_30',     '30 seconds remaining'),
    ('end_10',     '10 seconds remaining'),
    ('end_5',      '5 seconds remaining'),
]
ANNOUNCE_KEYS = [k for k, _l in ANNOUNCE_SOUNDS]

# default rows shown in the manager (any other key can be added there)
DEFAULT_NUM_KEYS = list(range(1, 11))
DEFAULT_CHAN_KEYS = ['r{}'.format(i) for i in range(1, 9)] + \
                    ['f{}'.format(i) for i in range(1, 9)]
CHAN_KEY_RE = re.compile(r'^[a-z][0-9]{1,2}$')

# countdown second-marks (before start / before time expiry) -> ann file key
START_MARKS = ((30, 'cd_30'), (10, 'cd_10'), (5, 'cd_5'))
END_MARKS = ((30, 'end_30'), (10, 'end_10'), (5, 'end_5'))


class SoundFxController:
    def __init__(self, rhapi):
        self._rhapi = rhapi
        self._last_play = {}    # (kind, key) -> monotonic ts, debounce
        self._sounds_dir = self._resolve_sounds_dir()
        self._speak_seq = itertools.count(1)
        self._cd_glets = {'start': [], 'end': []}   # scheduled countdown fires
        self._boot_ts = time.monotonic()            # mute startup HEAT_SET

    # ------------------------------------------------------------------ setup

    def _resolve_sounds_dir(self):
        base = None
        try:
            base = self._rhapi.server.data_dir
        except Exception:
            base = None
        if base:
            path = os.path.join(base, 'plugin_data', PLUGIN_ID)
        else:
            path = os.path.join(os.path.dirname(__file__), 'sounds')
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            logger.exception('Sound FX: cannot create sounds dir %s', path)
        return path

    def register_blueprint(self):
        bp = Blueprint(PLUGIN_ID, __name__, static_folder='static',
                       static_url_path='/sound_fx/static')

        @bp.route('/sound_fx/audio/<path:fname>')
        def sfx_audio(fname):
            return send_from_directory(self._sounds_dir, fname,
                                       max_age=0, conditional=True)

        @bp.route('/sound_fx/upload', methods=['POST'])
        def sfx_upload():
            return self._handle_upload()

        @bp.route('/sound_fx/player')
        def sfx_player():
            return Response(PLAYER_PAGE, mimetype='text/html')

        self._rhapi.ui.blueprint_add(bp)

    def on_startup(self, _args=None):
        self._register_ui()

    def _register_ui(self):
        ui = self._rhapi.ui
        fields = self._rhapi.fields
        ui.register_panel(PLUGIN_ID, 'Sound FX', 'settings', order=0)

        def opt(name, label, ftype, value, desc):
            fields.register_option(
                UIField(name=name, label=label, field_type=ftype,
                        value=value, desc=desc), PLUGIN_ID)

        opt(OPT_ENABLED, 'Enable sounds', UIFieldType.CHECKBOX, True,
            'Master switch. When off, no sounds are sent to any player.')
        opt(OPT_VOLUME, 'Volume (0-100)', UIFieldType.BASIC_INT, 100,
            'Playback volume applied in every browser player.')
        opt(OPT_PILOT_ON_LAP, 'Pilot sound on every lap',
            UIFieldType.CHECKBOX, True,
            'Play the pilot\'s personal sound each time they record a lap.')
        opt(OPT_PILOT_ON_HOLESHOT, 'Pilot sound on holeshot',
            UIFieldType.CHECKBOX, True,
            'Play the pilot\'s personal sound on their first gate pass.')
        opt(OPT_PILOT_ON_DONE, 'Pilot sound when pilot finishes',
            UIFieldType.CHECKBOX, False,
            'Play the pilot\'s personal sound when they complete the race.')
        opt(OPT_PILOT_ON_WIN, 'Pilot sound for the winner',
            UIFieldType.CHECKBOX, True,
            'Play the winner\'s personal sound when the race winner is declared.')
        opt(OPT_PILOT_REPLACES, 'Pilot sound replaces event sound',
            UIFieldType.CHECKBOX, True,
            'When a pilot has a personal sound, play it INSTEAD of the generic '
            'event sound. Off = play both.')
        opt(OPT_SERVER_PLAYBACK, 'Also play on server audio output',
            UIFieldType.CHECKBOX, False,
            'Play sounds on the timer\'s own audio jack/HDMI using mpg123, '
            'ffplay, mpg321, cvlc or omxplayer (first one found).')
        opt(OPT_LAP_NUMBER, 'Say lap number with pilot name',
            UIFieldType.CHECKBOX, True,
            'On every lap speak "<pilot> lap <N>" — needs number sounds '
            '(num_N) and optionally the word "lap" (announcements below).')
        opt(OPT_CD_START, 'Countdown to scheduled race start',
            UIFieldType.CHECKBOX, True,
            'When a race is scheduled, announce every full minute and '
            '30/10/5 seconds before the start (uses countdown sounds).')
        opt(OPT_CD_END, 'Countdown to end of a timed race',
            UIFieldType.CHECKBOX, True,
            'In fixed-time races announce minutes and 30/10/5 seconds '
            'remaining (uses countdown sounds).')
        opt(OPT_NEXT_GROUP, 'Announce next group on heat change',
            UIFieldType.CHECKBOX, True,
            'When a heat is selected, speak the "next group" prefix, then '
            'each pilot\'s sound followed by their channel sound.')
        fields.register_option(UIField(
            name=OPT_THEME, label='Run-page panel theme',
            field_type=UIFieldType.SELECT, value='dark',
            options=[
                UIFieldSelectOption('dark', 'Dark'),
                UIFieldSelectOption('light', 'Light'),
                UIFieldSelectOption('auto', 'Auto (browser)'),
            ],
            desc='Color scheme of the Sound FX panel on the Run page.'),
            PLUGIN_ID)

        # sound manager UI + browser player, injected into pages
        loader = ('<div id="sfx-manager"></div>'
                  '<script src="/sound_fx/static/sound_fx.js"></script>')
        ui.register_markdown(PLUGIN_ID, 'sfx_manager_boot', loader)
        player_only = '<script src="/sound_fx/static/sound_fx.js"></script>'
        for page in ('run', 'marshal', 'format'):
            panel = 'sound_fx_load_' + page
            ui.register_panel(panel, 'Sound FX player', page, order=0)
            ui.register_markdown(panel, 'sfx_boot_' + page, player_only)
            fields.register_option(UIField(
                name='_sfx_boot_' + page, label='', value='',
                field_type=UIFieldType.TEXT, private=True,
                desc=player_only), panel)

        self._broadcast_state()

    # -------------------------------------------------------------- option io

    def _opt(self, name, default=None):
        try:
            val = self._rhapi.db.option(name)
        except Exception:
            return default
        return default if val is None or val == '' else val

    def _opt_bool(self, name, default=False):
        return self._opt(name, default) in (True, 1, '1', 'true', 'True')

    def _volume(self):
        try:
            vol = int(float(self._opt(OPT_VOLUME, 100)))
        except (TypeError, ValueError):
            vol = 100
        return max(0, min(100, vol)) / 100.0

    def _rate(self):
        try:
            rate = int(float(self._opt(OPT_RATE, 100)))
        except (TypeError, ValueError):
            rate = 100
        return max(50, min(200, rate)) / 100.0

    # ----------------------------------------------------------------- files

    def _scan(self):
        '''Return {kind: {key: fname}} for every sound kind on disk.'''
        out = {'event': {}, 'pilot': {}, 'ann': {}, 'num': {}, 'chan': {}}
        try:
            names = os.listdir(self._sounds_dir)
        except OSError:
            return out
        for fname in names:
            stem, ext = os.path.splitext(fname)
            if ext.lower() not in ALLOWED_EXTS:
                continue
            if stem.startswith('event_'):
                out['event'][stem[6:]] = fname
            elif stem.startswith('pilot_'):
                try:
                    out['pilot'][int(stem[6:])] = fname
                except ValueError:
                    pass
            elif stem.startswith('ann_'):
                out['ann'][stem[4:]] = fname
            elif stem.startswith('num_'):
                try:
                    out['num'][int(stem[4:])] = fname
                except ValueError:
                    pass
            elif stem.startswith('chan_'):
                out['chan'][stem[5:].lower()] = fname
        return out

    def _remove_sound(self, kind, key):
        stem = '{}_{}'.format(kind, key)
        removed = False
        for ext in ALLOWED_EXTS:
            path = os.path.join(self._sounds_dir, stem + ext)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    removed = True
                except OSError:
                    logger.exception('Sound FX: cannot remove %s', path)
        return removed

    def _handle_upload(self):
        if 'file' not in request.files:
            return jsonify(ok=False, error='no file'), 400
        upload = request.files['file']
        kind = request.form.get('kind', '')
        key = str(request.form.get('key', '')).strip().lower()
        if kind not in ('event', 'pilot', 'ann', 'num', 'chan'):
            return jsonify(ok=False, error='bad kind'), 400
        if kind == 'event':
            if key not in [k for k, _e, _l in EVENT_SOUNDS]:
                return jsonify(ok=False, error='bad event key'), 400
        elif kind == 'ann':
            if key not in ANNOUNCE_KEYS:
                return jsonify(ok=False, error='bad announce key'), 400
        elif kind == 'chan':
            if not CHAN_KEY_RE.match(key):
                return jsonify(ok=False, error='bad channel (e.g. r1, f4)'), 400
        else:  # pilot / num
            try:
                num = int(key)
                if kind == 'num' and not (0 <= num <= 999):
                    raise ValueError
                key = str(num)
            except (TypeError, ValueError):
                return jsonify(ok=False, error='bad ' + kind + ' key'), 400
        ext = os.path.splitext(upload.filename or '')[1].lower()
        if ext not in ALLOWED_EXTS:
            return jsonify(ok=False,
                           error='allowed: ' + ', '.join(ALLOWED_EXTS)), 400
        # size guard (content_length may be absent; re-check after save)
        if request.content_length and request.content_length > MAX_UPLOAD_BYTES:
            return jsonify(ok=False, error='file too large'), 400

        self._remove_sound(kind, key)  # drop any older extension variant
        path = os.path.join(self._sounds_dir, '{}_{}{}'.format(kind, key, ext))
        try:
            upload.save(path)
            if os.path.getsize(path) > MAX_UPLOAD_BYTES:
                os.remove(path)
                return jsonify(ok=False, error='file too large'), 400
        except OSError:
            logger.exception('Sound FX: failed saving upload %s', path)
            return jsonify(ok=False, error='save failed'), 500
        logger.info('Sound FX: uploaded %s', os.path.basename(path))
        self._broadcast_state()
        return jsonify(ok=True, file=os.path.basename(path))

    # ----------------------------------------------------------------- state

    def _pilots(self):
        out = []
        try:
            pilots = self._rhapi.db.pilots or []
        except Exception:
            pilots = []
        for pilot in pilots:
            out.append({
                'id': pilot.id,
                'callsign': pilot.callsign or '',
                'name': pilot.name or '',
            })
        out.sort(key=lambda p: (p['callsign'] or p['name']).lower())
        return out

    def _url(self, fname):
        return '/sound_fx/audio/{}?v={}'.format(fname, int(time.time()))

    def _seat_channels(self):
        '''{seat: 'r1'-style chan key} for the current frequency profile.'''
        out = {}
        try:
            profile = self._rhapi.race.frequencyset
            freqs = json.loads(profile.frequencies)
            bands, chans = freqs.get('b') or [], freqs.get('c') or []
            for seat, band in enumerate(bands):
                chan = chans[seat] if seat < len(chans) else None
                if band and chan:
                    out[seat] = '{}{}'.format(band, chan).lower()
        except Exception:
            pass
        return out

    def _item(self, key, fname, label=None):
        d = {'key': key, 'file': fname,
             'url': self._url(fname) if fname else None}
        if label is not None:
            d['label'] = label
        return d

    def _state(self):
        files = self._scan()
        events = []
        for key, _evt, label in EVENT_SOUNDS:
            events.append(self._item(key, files['event'].get(key), label))
        pilots = []
        for pilot in self._pilots():
            fname = files['pilot'].get(pilot['id'])
            pilot.update({'file': fname,
                          'url': self._url(fname) if fname else None})
            pilots.append(pilot)
        announces = [self._item(key, files['ann'].get(key), label)
                     for key, label in ANNOUNCE_SOUNDS]
        num_keys = sorted(set(DEFAULT_NUM_KEYS) | set(files['num'].keys()))
        numbers = [self._item(n, files['num'].get(n)) for n in num_keys]
        seat_chans = self._seat_channels()
        chan_keys = sorted(set(DEFAULT_CHAN_KEYS) | set(files['chan'].keys())
                           | set(seat_chans.values()))
        channels = [self._item(c, files['chan'].get(c)) for c in chan_keys]
        return {
            'enabled': self._opt_bool(OPT_ENABLED, True),
            'volume': self._volume(),
            'rate': self._rate(),
            'lap_number': self._opt_bool(OPT_LAP_NUMBER, True),
            'cd_start': self._opt_bool(OPT_CD_START, True),
            'cd_end': self._opt_bool(OPT_CD_END, True),
            'next_group': self._opt_bool(OPT_NEXT_GROUP, True),
            'theme': self._opt(OPT_THEME, 'dark'),
            'events': events,
            'pilots': pilots,
            'announces': announces,
            'numbers': numbers,
            'channels': channels,
            'active_channels': sorted(set(seat_chans.values())),
        }

    def _broadcast_state(self):
        try:
            self._rhapi.ui.socket_broadcast(MSG_STATE, self._state())
        except Exception:
            logger.exception('Sound FX: state broadcast failed')

    # --------------------------------------------------------------- socket

    def on_get_state(self, _data=None):
        self._broadcast_state()

    def on_delete(self, data=None):
        data = data or {}
        kind = data.get('kind')
        key = data.get('key')
        if kind in ('event', 'pilot', 'ann', 'num', 'chan') and key is not None:
            self._remove_sound(kind, str(key).lower())
            self._broadcast_state()

    def on_test(self, data=None):
        data = data or {}
        kind = data.get('kind')
        key = data.get('key')
        files = self._scan()
        fname = None
        if kind in ('event', 'ann', 'chan'):
            fname = files.get(kind, {}).get(str(key).lower())
        elif kind in ('pilot', 'num'):
            try:
                fname = files[kind].get(int(key))
            except (TypeError, ValueError):
                fname = None
        if fname:
            self._speak([fname], 'test: {}'.format(fname), force=True)

    def on_ctl(self, data=None):
        '''Player control from any panel — rebroadcast to every player.'''
        data = data or {}
        action = data.get('action')
        if action in ('pause', 'resume', 'stop'):
            self._broadcast(MSG_CTL, {'action': action})
        elif action == 'rate':
            try:
                rate = int(float(data.get('rate', 100)))
            except (TypeError, ValueError):
                return
            rate = max(50, min(200, rate))
            try:
                self._rhapi.db.option_set(OPT_RATE, rate)
            except Exception:
                logger.exception('Sound FX: cannot store rate')
            self._broadcast(MSG_CTL, {'action': 'rate', 'rate': rate / 100.0})

    def on_set_enabled(self, data=None):
        data = data or {}
        val = '1' if data.get('enabled') else '0'
        try:
            self._rhapi.db.option_set(OPT_ENABLED, val)
        except Exception:
            logger.exception('Sound FX: cannot store enabled flag')
        self._broadcast_state()

    def on_announce(self, data=None):
        '''Manual announcement buttons on the Run-page panel.'''
        data = data or {}
        key = data.get('key')
        if key == 'arm':
            files = self._scan()
            fname = files['ann'].get('arm')
            if fname:
                self._speak([fname], 'Arm your quads', force=True)
        elif key == 'next_group':
            self._announce_next_group(force=True)

    def _broadcast(self, msg, payload):
        try:
            self._rhapi.ui.socket_broadcast(msg, payload)
        except Exception:
            logger.exception('Sound FX: broadcast %s failed', msg)

    # --------------------------------------------------------------- events

    def _pilot_file(self, files, pilot_id):
        if pilot_id in (None, 0):
            return None
        try:
            return files['pilot'].get(int(pilot_id))
        except (TypeError, ValueError):
            return None

    def _callsign(self, pilot_id):
        try:
            pilot = self._rhapi.db.pilot_by_id(int(pilot_id))
            return pilot.callsign or pilot.name or 'pilot {}'.format(pilot_id)
        except Exception:
            return 'pilot {}'.format(pilot_id) if pilot_id else ''

    def _handle_event(self, evt_key, pilot_id=None, suffix=None, label=None):
        '''Compose one phrase for the event: [event sound] [pilot sound]
        [suffix parts, e.g. lap number] — options decide which pieces play.'''
        if not self._opt_bool(OPT_ENABLED, True):
            return
        files = self._scan()
        parts = []
        pilot_part = None
        trigger_opt = PILOT_TRIGGER_OPTS.get(evt_key)
        if trigger_opt and self._opt_bool(trigger_opt,
                                          trigger_opt != OPT_PILOT_ON_DONE):
            pilot_part = self._pilot_file(files, pilot_id)
        event_part = files['event'].get(evt_key)
        if event_part and not (pilot_part and
                               self._opt_bool(OPT_PILOT_REPLACES, True)):
            parts.append(event_part)
        if pilot_part:
            parts.append(pilot_part)
            parts.extend(suffix or [])
        if not parts:
            return
        if not label:
            label = dict((k, l) for k, _e, l in EVENT_SOUNDS).get(
                evt_key, evt_key)
        self._speak(parts, label,
                    dedupe_key='{}:{}'.format(evt_key, pilot_id or ''))

    def on_lap_recorded(self, args=None):
        args = args or {}
        pilot_id = args.get('pilot_id')
        lap = args.get('lap')
        lap_number = getattr(lap, 'lap_number', None)
        if lap_number is None and isinstance(lap, dict):
            lap_number = lap.get('lap_number')
        evt_key = 'holeshot' if lap_number == 0 else 'lap'
        suffix, label = [], None
        if (evt_key == 'lap' and lap_number and
                self._opt_bool(OPT_LAP_NUMBER, True)):
            files = self._scan()
            num = files['num'].get(int(lap_number))
            if num:
                word = files['ann'].get('lap_word')
                suffix = ([word] if word else []) + [num]
            label = '{} — lap {}'.format(self._callsign(pilot_id), lap_number)
        elif evt_key == 'holeshot':
            label = '{} — holeshot'.format(self._callsign(pilot_id))
        self._handle_event(evt_key, pilot_id, suffix=suffix, label=label)

    def on_race_win(self, args=None):
        args = args or {}
        pilot_id = None
        win_status = args.get('win_status') or {}
        data = win_status.get('data') or {}
        if isinstance(data, dict):
            pilot_id = data.get('pilot_id')
        label = '{} wins'.format(self._callsign(pilot_id)) if pilot_id else None
        self._handle_event('win', pilot_id, label=label)

    def on_pilot_done(self, args=None):
        args = args or {}
        pilot_id = args.get('pilot_id')
        label = ('{} finished'.format(self._callsign(pilot_id))
                 if pilot_id else None)
        self._handle_event('pilot_done', pilot_id, label=label)

    def make_simple_handler(self, evt_key):
        def handler(_args=None):
            self._handle_event(evt_key)
        return handler

    def on_pilot_delete(self, args=None):
        args = args or {}
        pilot_id = args.get('pilot_id')
        if pilot_id is not None:
            self._remove_sound('pilot', pilot_id)
            self._broadcast_state()

    def on_pilot_alter(self, _args=None):
        self._broadcast_state()

    def on_option_set(self, args=None):
        args = args or {}
        if str(args.get('option', '')).startswith('sfx_'):
            self._broadcast_state()

    # ------------------------------------------------------------ countdowns

    def _cancel_countdown(self, which):
        for glet in self._cd_glets.get(which, []):
            try:
                glet.kill(block=False)
            except Exception:
                pass
        self._cd_glets[which] = []

    def _cd_fire(self, which, minutes=None, mark_key=None):
        '''Fires at one countdown mark: N whole minutes or a seconds mark.'''
        opt = OPT_CD_START if which == 'start' else OPT_CD_END
        if not (self._opt_bool(OPT_ENABLED, True) and
                self._opt_bool(opt, True)):
            return
        files = self._scan()
        if minutes is not None:
            tail = files['ann'].get(
                'cd_minutes' if which == 'start' else 'end_minutes')
            num = files['num'].get(minutes)
            if not (num and tail):
                return
            label = '{} minute{} {}'.format(
                minutes, '' if minutes == 1 else 's',
                'to start' if which == 'start' else 'remaining')
            self._speak([num, tail], label, priority=True, force=True)
        elif mark_key:
            fname = files['ann'].get(mark_key)
            if not fname:
                return
            secs = mark_key.rsplit('_', 1)[-1]
            label = '{} seconds {}'.format(
                secs, 'to start' if which == 'start' else 'remaining')
            self._speak([fname], label, priority=True, force=True)

    def _schedule_countdown(self, which, total_secs):
        '''Spawn timers for minute marks + 30/10/5 s within total_secs.'''
        self._cancel_countdown(which)
        glets = []
        marks = START_MARKS if which == 'start' else END_MARKS
        for minutes in range(1, int(total_secs // 60) + 1):
            delay = total_secs - minutes * 60
            if delay > 0.5:
                glets.append(gevent.spawn_later(
                    delay, self._cd_fire, which, minutes=minutes))
        for secs, key in marks:
            delay = total_secs - secs
            if delay > 0.5:
                glets.append(gevent.spawn_later(
                    delay, self._cd_fire, which, mark_key=key))
        self._cd_glets[which] = glets

    def on_race_schedule(self, args=None):
        args = args or {}
        scheduled_at = args.get('scheduled_at')   # monotonic timestamp
        if scheduled_at is None:
            return
        remaining = scheduled_at - time.monotonic()
        if remaining > 5:
            self._schedule_countdown('start', remaining)

    def on_race_schedule_cancel(self, _args=None):
        self._cancel_countdown('start')

    def on_race_stage(self, _args=None):
        # staging tones take over from here; the start delay is random anyway
        self._cancel_countdown('start')

    def on_race_start(self, _args=None):
        self._cancel_countdown('start')
        try:
            fmt = self._rhapi.race.raceformat
            timed = fmt and not getattr(fmt, 'unlimited_time', 1)
            duration = int(getattr(fmt, 'race_time_sec', 0) or 0)
        except Exception:
            timed, duration = False, 0
        if timed and duration >= 15 and self._opt_bool(OPT_CD_END, True):
            self._schedule_countdown('end', duration)

    def on_race_stop(self, _args=None):
        self._cancel_countdown('end')

    def on_race_finish(self, _args=None):
        self._cancel_countdown('end')

    # ------------------------------------------------------- next-group call

    def on_heat_set(self, args=None):
        if time.monotonic() - self._boot_ts < 10:
            return    # heat restored during server startup — stay quiet
        if not (self._opt_bool(OPT_ENABLED, True) and
                self._opt_bool(OPT_NEXT_GROUP, True)):
            return
        heat_id = (args or {}).get('heat_id')
        self._announce_next_group(dedupe_key='next_group:{}'.format(heat_id))

    def _announce_next_group(self, dedupe_key=None, force=False):
        '''"Next group" prefix, then each seated pilot's sound + channel.'''
        files = self._scan()
        parts = []
        prefix = files['ann'].get('next_group')
        if prefix:
            parts.append(prefix)
        try:
            seat_pilots = dict(self._rhapi.race.pilots or {})
        except Exception:
            seat_pilots = {}
        seat_chans = self._seat_channels()
        names = []
        for seat in sorted(seat_pilots.keys()):
            pilot_id = seat_pilots[seat]
            if not pilot_id:
                continue
            pilot_part = self._pilot_file(files, pilot_id)
            chan_key = seat_chans.get(seat)
            chan_part = files['chan'].get(chan_key) if chan_key else None
            if pilot_part:
                parts.append(pilot_part)
            if chan_part:
                parts.append(chan_part)
            if pilot_part or chan_part:
                names.append('{} ({})'.format(
                    self._callsign(pilot_id),
                    (chan_key or '?').upper()))
        if not names:
            return
        self._speak(parts, 'Next group: ' + ', '.join(names),
                    dedupe_key=dedupe_key or 'next_group', force=force,
                    dedupe_secs=5.0)

    # -------------------------------------------------------------- playback

    def _speak(self, fnames, label, priority=False, dedupe_key=None,
               force=False, dedupe_secs=0.4):
        '''Queue one phrase (a sequence of sound files) on every player.

        Browsers play phrases one after another; `priority` phrases (the
        countdown marks) jump the queue and interrupt whatever is playing.'''
        fnames = [f for f in (fnames or []) if f]
        if not fnames:
            return
        if dedupe_key:
            now = time.monotonic()
            if not force and \
                    now - self._last_play.get(dedupe_key, 0) < dedupe_secs:
                return
            self._last_play[dedupe_key] = now
        payload = {
            'id': next(self._speak_seq),
            'label': label or fnames[0],
            'parts': [{'url': self._url(f), 'file': f} for f in fnames],
            'volume': self._volume(),
            'rate': self._rate(),
            'priority': bool(priority),
            'ts': int(time.time() * 1000),
        }
        self._broadcast(MSG_SPEAK, payload)
        if self._opt_bool(OPT_SERVER_PLAYBACK, False):
            paths = [os.path.join(self._sounds_dir, f) for f in fnames]
            gevent.spawn(self._play_on_server_seq, paths)

    _server_players = (
        ('mpg123', ['-q']),
        ('mpg321', ['-q']),
        ('ffplay', ['-nodisp', '-autoexit', '-loglevel', 'quiet']),
        ('cvlc', ['--play-and-exit', '--quiet']),
        ('omxplayer', []),
    )

    def _play_on_server_seq(self, paths):
        # parts of one phrase must not overlap — wait for each player process
        for path in paths:
            self._play_on_server(path, wait=True)

    def _play_on_server(self, path, wait=False):
        for player, player_args in self._server_players:
            exe = shutil.which(player)
            if not exe:
                continue
            if player.startswith('mpg') and not path.lower().endswith('.mp3'):
                continue  # mpg123/321 decode mp3 only
            try:
                proc = subprocess.Popen([exe] + player_args + [path],
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
                if wait:
                    proc.wait()
            except OSError:
                logger.exception('Sound FX: server playback failed (%s)', player)
            return
        logger.warning('Sound FX: no audio player found on server '
                       '(tried mpg123, mpg321, ffplay, cvlc, omxplayer)')


# Minimal standalone player page: open http://<timer>/sound_fx/player on any
# device with speakers. One tap enables audio (browser autoplay policy).
PLAYER_PAGE = '''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RotorHazard Sound FX player</title>
<style>
  body { margin:0; font-family:system-ui,sans-serif; background:#14181d;
         color:#e8edf2; display:flex; min-height:100vh; align-items:center;
         justify-content:center; flex-direction:column; gap:24px; }
  #arm { font-size:1.4em; padding:20px 44px; border-radius:12px; border:0;
         background:#2f81f7; color:#fff; cursor:pointer; }
  #arm.armed { background:#238636; }
  #log { font-size:.95em; color:#9aa6b2; max-width:90vw; text-align:center;
         min-height:3em; white-space:pre-line; }
</style>
</head>
<body>
<h2>RotorHazard &mdash; Sound FX player</h2>
<button id="arm">Tap to enable audio</button>
<div id="log">Waiting&hellip;</div>
<script src="/static/socket.io-4.6.1/socket.io.min.js"
        onerror="document.getElementById('log').textContent='socket.io not found on this server version'"></script>
<script src="/sound_fx/static/sound_fx.js"></script>
</body>
</html>
'''
