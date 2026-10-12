# flaggarr

Decides which audio track and which subtitle track every film and episode in
Plex starts with, by rules you set, on every TV, phone and browser, without
anyone opening a menu.

The rules are yours. Which languages you understand and which you read. For
each spoken language: no subtitles, subtitles in the same language, or
subtitles in your language. Whether forced subtitles, SDH, Blu-ray and DVD
picture subtitles may be used at all, and in which order. Which audio codec
wins when a file carries several. A second set of rules for titles carrying a
tag, so dubbed animation can follow its own order. Each person your Plex
server is shared with can answer a few questions and get their own rules,
while the files stay yours.

Plex has no setting for any of this. It remembers whatever track was last
chosen for a title, and new files arrive with whatever track the release group
marked as default. flaggarr fixes both: it marks the right tracks inside the
file as it arrives, and corrects the choice Plex has saved.

## What you need

- **Plex** - the media server. flaggarr talks to it to re-read new files and
  to correct the saved track choice.
- **Radarr** (films) and **Sonarr** (series) - the programs that download and
  file your library. They tell flaggarr when a file has arrived, and which
  language it was made in. Without them nothing triggers.
- **Bazarr** - optional. It fetches subtitles for Radarr and Sonarr. flaggarr
  only needs it for the cleanup job that removes subtitle files in languages
  you do not want; everything else works without it.
- **Docker** - flaggarr runs as one container, next to the others.

Built for a Synology NAS. Nothing in the code is Synology-specific, but every
default path and the time zone are shaped for one, so expect to change them
elsewhere.

What it does in detail, the rules, and every script are described in
[REFERENCE.md](REFERENCE.md). Every setting, its default value and what it
means is listed in [DEFAULTS.md](DEFAULTS.md).

## How it works

When Radarr or Sonarr files a new download it sends flaggarr a message (a
webhook). flaggarr then marks the right audio and subtitle track as the default
inside the file (mkv only - other formats cannot carry the mark), asks Plex to
re-read the folder, and corrects the track Plex has saved for that title. The
last step covers what the mark cannot: external .srt files, mp4, m4v and mov,
and titles Plex had already made a choice for.

A weekly pass over the whole library catches anything that was missed. Two
cleanup jobs remove subtitle files in languages nobody wants and the leftovers
Radarr and Sonarr leave behind.

The settings all live on a web page on port 8899. `compose.yaml` holds the
things a web page cannot set: the mounts, the port and the user.

## Install

1. Make a folder for it, for example `/volume1/docker/flaggarr` on a Synology,
   and put a copy of [`compose.example.yaml`](compose.example.yaml) there as
   `compose.yaml`. It pulls the ready-made image
   `ghcr.io/ektoplasmap/flaggarr`, so there is nothing to clone or build.
2. Point the media volume at your library. That path must be the **same inside
   and outside** the container, so the paths Radarr and Sonarr send in the
   webhook work without translation. `PUID`/`PGID` start commented out, which
   runs the program as root - that works everywhere, and you can narrow it
   later (see below).
3. Start it. On a Synology: Container Manager -> Project -> create a project
   pointing at this folder. Elsewhere: `docker compose up -d`.
4. Open `http://<your-nas>:8899/`. It asks you to pick a password before it
   shows anything else. After that a **Quick start** wizard asks
   four questions, and the rest of the page stays out of reach until it is
   finished or skipped.
5. Fill in Connections (Plex, Radarr, Sonarr, Bazarr). Check **Media root**
   under Media: nothing is touched outside it, so an import on a path it does
   not cover is dropped. Then set your languages under Languages, and the track
   types and codecs under Tracks.
6. In Radarr and Sonarr, add the webhook address shown under Automation, with
   the triggers On Import and On Upgrade. On Rename works too, which is worth
   having after a library reorganisation.

Nothing is written to your library until you say so: every job has a dry run.
The cleanup jobs follow the **Use quarantine** switch on the Cleanup page. On,
which is the default, a removed file is moved to the quarantine folder with a
manifest and can be put back. Off, those jobs delete for good, and the weekly
run does it unattended.

The program is `app/`, and it is copied into the image at build time. `config/`
holds what this installation owns: `settings.json`, `log/`, `backup/`, `undo/`,
`tag_map.json`, and the password-reset files. It is created on first run, so a
clone starts from the built-in defaults, which is why `config/` is gitignored.

## Updating

Pull the new image and start again: Container Manager -> Project flaggarr ->
Build (it pulls when there is nothing to build), or `docker compose pull &&
docker compose up -d` elsewhere. Settings are read on every event, so a
settings change never needs a restart, but the code lives in the image and
does need the new one.

To run from a clone of this repo instead, swap the `image:` line in
`compose.yaml` for `build: .` as the example shows, and build after each
pull. Your `compose.yaml` is gitignored, so your own paths stay out of the repo.

## The version

`VERSION` in `app/ui.py` is the only place the version is written, and the
footer on the page reads it directly, so the footer is always true. The build
also stamps it onto the image as OCI metadata, which is what `docker inspect`
and Container Manager report. The published image gets it from the same file:
every push to `main` becomes `:latest`, and a tag such as `v0.87.0` also
becomes `:0.87.0`.

Do not put the version in an image tag by hand when you build yourself. A tag
typed once stays where it was typed and then quietly disagrees with the code.
`compose.example.yaml` pins no number for the same reason, so a plain build
labels the image `dev`. To stamp the real number, let the build read the file:

    docker compose build --build-arg VERSION="$(sed -n 's/^VERSION *= *"\([^"]*\)".*/\1/p' app/ui.py)"

## Which user it runs as

The container starts as root, hands `/config` to the user in `PUID`/`PGID`, and
drops to that user before the program starts. They ship commented out, so out
of the box it runs as root, which works everywhere but is more than it needs.
Narrowing later costs nothing. Point them at the same account your other *arr
containers use, one with read/write on the media library, because mkvpropedit
writes into the files and the cleanup jobs move them. `compose.example.yaml`
says how to find your own numbers.

On a Synology, ownership alone is not always enough: a folder that was created
from File Station or DSM follows its ACL rather than the Unix owner. If the
program reports that `/config` is not writable even though the files belong to
that user, give the user Read/Write on the folder in File Station (Properties ->
Permission, applied to sub-folders and files), and the same on the media share.

## Sign-in

A password is not optional. flaggarr writes into your media files and moves
subtitles aside, so there is no mode where it runs without one at all.

The first time you open the page it asks you to pick a password and offers
nothing else: no settings, no jobs, and no data of any kind until there is
one. There is no username - the password is the whole sign-in. Imports keep
working while you are there, because the webhook from Radarr and Sonarr is not
part of the page.

After that the password is required from everywhere, the home network
included. **Require sign-in on local network** on the Security page is on by
default; turn it off to let the home network in without signing in, while
anything further away still needs the password.

What counts as the home network is the address the request really arrived from,
read off the socket and never off a header, because `X-Forwarded-For` and
`X-Real-IP` are written by whoever is talking to us. Private ranges, loopback
and overlay networks like Tailscale count as local; a public address does not,
so a router port-forwarding `:8899` straight to the container is treated as
remote and still needs the password.

One case the program cannot see through: if Docker publishes the port through
its userland proxy, every request reaches the container from the bridge gateway
and so looks local. That is the reason the switch starts on rather than off.

Forgot the password? Create an empty file `config/RESET_PASSWORD` and the next
request sets a fresh random password, written to `config/NEW_PASSWORD.txt`.

## Everyone who shares your Plex server

The flags inside the files are the same for everyone, but the track Plex saves
is per account. So every person your server is shared with can have rules of
their own, and flaggarr applies them for that account after it has applied
yours. A child who only wants dubbed audio, a partner who reads a different
language, a parent who needs subtitles on everything: each gets what they
asked for, on every device they sign in to, and nobody has to touch a menu.

They set it up themselves. On the sign-in page, **Continue with Plex** takes a
plex.tv PIN, so no password ever reaches flaggarr, and shows them one page of
their own and nothing else. Six questions:

1. **Hearing** - hard of hearing? Subtitles on for every language, and the
   hearing-impaired version first when there is one.
2. **Languages** - the languages they understand, in order.
3. **Audio** - original language, or dubbed into theirs.
4. **Subtitles** - only when the film is foreign, always, or never.
5. **Forced** - forced lines only when there is no full track, or also for
   languages they understand.
6. **Finish** - a summary, with *I'm happy with this* or *Fine-tune*.

**Fine-tune** opens the full editor, the same per-language tables, subtitle
types and codec rules you have, with their answers already filled in. A link
on the first screen, *Just use the administrator's defaults*, keeps their
profile off, and they follow your settings. Coming back later lands on a page
that shows what is saved, with *Start over*, *View summary* and *Fine-tune*.

You see all of it on the **Users** page: every account the server is shared
with, which ones set themselves up and when, and the profile behind each card.
You can edit a profile, which asks first since the account is not told, reset
one to follow your settings again, or reset all of them. Two settings under
Tracks reach every account whatever their profile says: **Never use
image-based subtitles** and **Use tag rules for all users**. **Demo mode**
walks through the guest page in a sandbox where nothing is saved, so you can
see what they will see.

A profile only takes effect once it is enabled. Answers filled in on a profile
that is switched off change nothing, and that account follows your settings.

## Notes

* While Overview, Run, Log, Cleanup or Statistics is showing, the page keeps
  one event stream open and fetches only when the server says something
  changed: a job starting or finishing, an import arriving, the undo running.
  If the stream cannot be held it falls back to fetching every five seconds
  during a job and once a minute otherwise. A **Refresh** link at the bottom of
  the menu fetches on demand.
* `docker logs -f flaggarr` follows what the listener is doing; the same lines
  are on the Log page.
* The weekly check and the cleanup jobs are started from the Run page. To run
  one from a shell instead:

      docker exec flaggarr sh /app/run_trashfiles_cleanup.sh apply

  `apply` moves files to quarantine, `delete` removes them for good, and
  anything else is a dry run. Started this way the job only covers **Media
  root**: a separately configured movie or series path is skipped unless you
  pass `MEDIA_ROOTS` yourself.
* The About page links to README.md, REFERENCE.md and DEFAULTS.md on GitHub,
  so the documentation you read is always the current one.

## License

MIT. See [LICENSE](LICENSE).
