# RotorHazard Sound FX

Play custom MP3/WAV/OGG sounds on race events — and a **personal sound for
every pilot** (e.g. their name callout), played when that pilot records a
lap, scores the holeshot, finishes or wins.

The plugin speaks **composed phrases** built from your sound files: pilot
name + lap number on every pass, "next group" announcements with each
pilot's name and channel, countdowns to a scheduled race start and to the
end of a timed race. A **Run-page panel** shows a status lamp, the phrase
being spoken with a mini player (progress bar, play / pause / stop), a live
queue feed and playback-speed / volume sliders.

Sounds are uploaded straight from the RotorHazard **Settings** page and play
in **every connected browser**. A standalone player page turns any phone,
tablet or PC on the network into a dedicated speaker, and playback on the
timer's own audio output (headphone jack / HDMI) can be enabled too.

No extra Python dependencies.

## Features

- **Event sounds** — upload a sound for any of these events:

  | Event | Fires when |
  |---|---|
  | Race scheduled | a race is scheduled |
  | Race staging (arm) | staging begins |
  | Race start | the start signal |
  | Race finish | race time expires |
  | Race stop | the race is stopped |
  | Winner declared | the winner is announced |
  | Holeshot | first gate pass of any pilot |
  | Lap recorded | any pilot records a lap |
  | Pilot finished | a pilot completes the race |
  | Race saved | laps are saved |
  | Rounds complete | all rounds of the class are done |

- **Pilot sounds** — one personal file per pilot. Triggers are individually
  selectable in the options:
  - on every lap *(default on)*
  - on the holeshot *(default on)*
  - when the pilot finishes *(default off)*
  - when the pilot is declared the winner *(default on)*

  By default the pilot sound **replaces** the generic event sound (so you
  hear "VOLT!" instead of the plain lap beep); turn *Pilot sound replaces
  event sound* off to hear both.

- **Composed phrases** *(v1.1)* — phrases are queued and spoken one after
  another, several sound files back to back:
  - **Pilot + lap number** — "*VOLT … lap … 3*": pilot sound, optional
    word "lap" (`ann_lap_word`) and a number sound (`num_3`). Option *Say
    lap number with pilot name*.
  - **Next group** — on heat selection (and from the 📢 group button):
    "next group" prefix, then each seated pilot's sound followed by their
    channel callout (`chan_r1`, `chan_f4`, …). Option *Announce next group
    on heat change*.
  - **Countdown to a scheduled start** — every full minute ("*2 …
    minutes-to-start*" = `num_2` + `ann_cd_minutes`), then 30 / 10 / 5
    seconds (`ann_cd_30/10/5`). Option *Countdown to scheduled race start*.
  - **Countdown to the end of a timed race** — minutes remaining
    (`num_N` + `ann_end_minutes`) and 30 / 10 / 5 s (`ann_end_30/10/5`).
    Option *Countdown to end of a timed race*.
  - **Manual announcements** — "arm your quads" (`ann_arm`) and "next
    group" buttons right on the Run-page panel.
  - **Custom announcements** *(v1.1.1)* — upload any sound as
    `say_<name>` ("clear the track", "lunch break", …) in the *Custom
    announcements* manager section and it gets its own 📢 button on the
    Run-page panel. The **Label** field sets the button text (any
    language); without it the name is used (`_` → space). Announcement
    buttons sit on their own wrapping row, so any number of them fits.

  Countdown marks are **priority** phrases: they jump the queue and
  interrupt whatever is being spoken, so "5 seconds" never arrives late.

- **Run-page panel** *(v1.1)* — lives in the shared plugin dock above the
  pilot table (next to Gate Walkthrough Calibration / Claude Auto
  Marshalling), collapsed to a slim bar until something plays:
  - **status lamp** — green: the plugin voices events; amber: enabled but
    this browser still needs one click before audio may play; gray: off;
  - **mini player** — the phrase being spoken, progress bar, elapsed /
    total time, ⏵ ⏸ ⏹ (controls act on **every** connected player);
  - **live feed** — phrases waiting in the queue; a spoken phrase
    disappears and the next one moves onto the player bar;
  - **speed slider** — 0.5×–2× playback rate for all players (persisted);
  - **volume slider** — 0–100 % playback volume for all players, live
    while dragging (persisted, same value as *Volume* in Settings);
  - **SFX ON/OFF** master switch and the announcement buttons.

- **Where it plays**
  - only the **Run page** (and the standalone player below) is audible;
    Settings / Marshal / Format follow the phrase queue silently. With
    several Run tabs in one browser a single leader tab is elected, so
    duplicate tabs never produce a phasey "echo". A 🔊/🔇 button on the
    Run-page panel mutes the whole device (e.g. the laptop, when a
    dedicated speaker is connected). The ▶ preview buttons in the manager
    stay audible everywhere;
  - on any device that opens `http://<timer-ip>/sound_fx/player` — a
    minimal page with a single *Tap to enable audio* button, made to be a
    dedicated speaker client;
  - optionally on the **server itself** (option *Also play on server audio
    output*) via the first player found among `mpg123`, `mpg321`, `ffplay`,
    `cvlc`, `omxplayer`. On a Raspberry Pi: `sudo apt install mpg123`.

- **Manager UI** — upload / replace / preview (▶) / play-everywhere (📢) /
  remove (✕) each sound from the *Sound FX* panel on the Settings page.
  Six sections: event sounds, pilot sounds, announcements & countdown
  pieces, numbers (for laps and minutes — add rows for any number up to
  999), channel callouts (R1–R8 / F1–F8 by default, channels of the
  current frequency set are marked ●, any band+channel can be added) and
  custom announcements (each uploaded sound gets a 📢 button on the
  Run-page panel). Files up to 20 MB; `.mp3`, `.wav`, `.ogg`, `.m4a`.

- **Housekeeping** — a pilot's sound is deleted automatically when the pilot
  is deleted; global volume option (0–100); master enable switch.

## Install

Copy the `custom_plugins/sound_fx` folder into your RotorHazard
`src/server/plugins/` directory and restart the server:

```bash
cd ~
wget https://github.com/izicubed/RotorHazard-Sound-FX/archive/refs/heads/main.zip
unzip main.zip
cp -r RotorHazard-Sound-FX-main/custom_plugins/sound_fx ~/RotorHazard/src/server/plugins/
sudo systemctl restart rotorhazard
```

Or install through the RotorHazard plugin manager once listed in the
community catalog.

## Usage

1. Open **Settings → Sound FX**.
2. Set the options (volume, pilot-sound triggers).
3. In the panel below the options, upload files for the events you want and
   for each pilot.
4. Use ▶ to preview locally, 📢 to test on every connected player.
5. Browsers block autoplay until the page is interacted with once — click
   anywhere on the page (or tap the button on the standalone player) after
   loading it.

Uploaded files are stored in the server data directory
(`<data_dir>/plugin_data/sound_fx/`), so they survive plugin updates.

## Notes

- Sound files are broadcast to all clients over socket.io as URLs; clients
  fetch and play them locally, so network load is minimal.
- The per-lap debounce is 0.4 s per sound, preventing double-fires.
- Tested with RotorHazard 4.3 and 4.4.

## License

MIT NON-AI License — see [LICENSE](LICENSE).
