'''
Sound FX plugin for RotorHazard.

Plays uploaded MP3/WAV/OGG files on race events (staging, start, finish,
stop, winner, laps, holeshot, pilot finished, race saved, ...) and a
personal per-pilot sound — e.g. a pilot-name callout — when that pilot
records a lap, scores the holeshot, finishes or wins. Sounds are managed
from the Settings page and played in every connected browser; a
standalone /sound_fx/player page turns any device into a speaker, and
playback on the server's own audio output can be enabled as well.
'''

from eventmanager import Evt
from .sound_fx import (
    SoundFxController, EVENT_SOUNDS,
    EV_GET_STATE, EV_DELETE, EV_TEST, EV_CTL, EV_SET_ENABLED, EV_ANNOUNCE,
)


def initialize(rhapi):
    controller = SoundFxController(rhapi)
    controller.register_blueprint()

    rhapi.events.on(Evt.STARTUP, controller.on_startup,
                    name='sound_fx_startup')

    # race events with a pilot attached get dedicated handlers
    rhapi.events.on(Evt.RACE_LAP_RECORDED, controller.on_lap_recorded,
                    name='sound_fx_lap')
    rhapi.events.on(Evt.RACE_WIN, controller.on_race_win,
                    name='sound_fx_win')
    rhapi.events.on(Evt.RACE_PILOT_DONE, controller.on_pilot_done,
                    name='sound_fx_pilot_done')

    # countdown to a scheduled start / to the end of a timed race, and the
    # next-group announcement when a heat is selected
    rhapi.events.on(Evt.RACE_SCHEDULE, controller.on_race_schedule,
                    name='sound_fx_cd_schedule')
    rhapi.events.on(Evt.RACE_SCHEDULE_CANCEL, controller.on_race_schedule_cancel,
                    name='sound_fx_cd_cancel')
    rhapi.events.on(Evt.RACE_STAGE, controller.on_race_stage,
                    name='sound_fx_cd_stage')
    rhapi.events.on(Evt.RACE_START, controller.on_race_start,
                    name='sound_fx_cd_start')
    rhapi.events.on(Evt.RACE_STOP, controller.on_race_stop,
                    name='sound_fx_cd_stop')
    rhapi.events.on(Evt.RACE_FINISH, controller.on_race_finish,
                    name='sound_fx_cd_finish')
    rhapi.events.on(Evt.HEAT_SET, controller.on_heat_set,
                    name='sound_fx_next_group')

    # plain event sounds (lap/holeshot/win/pilot_done are handled above)
    _special = ('lap', 'holeshot', 'win', 'pilot_done')
    for key, evt, _label in EVENT_SOUNDS:
        if evt is None or key in _special:
            continue
        rhapi.events.on(evt, controller.make_simple_handler(key),
                        name='sound_fx_' + key)

    # keep the pilot list and options in the manager UI fresh
    rhapi.events.on(Evt.PILOT_ADD, controller.on_pilot_alter,
                    name='sound_fx_pilot_add')
    rhapi.events.on(Evt.PILOT_ALTER, controller.on_pilot_alter,
                    name='sound_fx_pilot_alter')
    rhapi.events.on(Evt.PILOT_DELETE, controller.on_pilot_delete,
                    name='sound_fx_pilot_delete')
    rhapi.events.on(Evt.OPTION_SET, controller.on_option_set,
                    name='sound_fx_option_set')

    rhapi.ui.socket_listen(EV_GET_STATE, controller.on_get_state)
    rhapi.ui.socket_listen(EV_DELETE, controller.on_delete)
    rhapi.ui.socket_listen(EV_TEST, controller.on_test)
    rhapi.ui.socket_listen(EV_CTL, controller.on_ctl)
    rhapi.ui.socket_listen(EV_SET_ENABLED, controller.on_set_enabled)
    rhapi.ui.socket_listen(EV_ANNOUNCE, controller.on_announce)
