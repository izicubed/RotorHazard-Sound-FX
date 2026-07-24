# RotorHazard Sound FX

Play custom MP3/WAV/OGG sounds on race events — and a **personal sound for
every pilot** (e.g. their name callout), played when that pilot records a
lap, scores the holeshot, finishes or wins.

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

- **Where it plays**
  - in every open RotorHazard page (Run, Settings, Marshal, Format);
  - on any device that opens `http://<timer-ip>/sound_fx/player` — a
    minimal page with a single *Tap to enable audio* button, made to be a
    dedicated speaker client;
  - optionally on the **server itself** (option *Also play on server audio
    output*) via the first player found among `mpg123`, `mpg321`, `ffplay`,
    `cvlc`, `omxplayer`. On a Raspberry Pi: `sudo apt install mpg123`.

- **Manager UI** — upload / replace / preview (▶) / play-everywhere (📢) /
  remove (✕) each sound from the *Sound FX* panel on the Settings page.
  Files up to 20 MB; `.mp3`, `.wav`, `.ogg`, `.m4a`.

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
