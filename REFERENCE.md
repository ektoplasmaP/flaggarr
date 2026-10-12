# Audio and subtitle selection for Plex

The program lives in `app/`, copied into the image as `/app`. `config/` is the
mounted volume, `/config` inside the container, and holds what this
installation owns: `settings.json`, `log/`, `backup/`, `undo/`, `tag_map.json`
and the password-reset files. Sets the default audio and subtitle flags in mkv
files, and corrects the audio and subtitle track Plex has saved.

The version is in the footer on the page, read straight out of `app/ui.py`. The
About page shows the address the page was opened on; there is no address
setting any more.

## Two mechanisms, not one

The default is set in two completely different ways, and which one applies
depends on where the subtitle lives.

| Where the subtitle is | What decides the default |
|---|---|
| Inside an mkv (SRT) | the default flag in the file |
| Inside an mp4/m4v/mov (mov_text) | the choice saved in Plex |
| An external .srt next to the file | the choice saved in Plex |
| PGS, VobSub | never picked unless their type is allowed under Tracks |
| ASS/SSA | never picked unless `sub_ass` allows it, which the image switch does not govern |

The flag is a property of a track **inside** the file. An external .srt can
therefore never be flagged - there the choice saved in Plex is the only way. The
other way round: VLC, Infuse and TV apps only read the flag and know nothing
about what Plex has saved.

mkvpropedit can only write Matroska. Mp4-based files (mp4, m4v) carry their
subtitles as `mov_text` and are never touched by the flag script - there
auto_set_plex_defaults.py does the whole job. Bazarr counts mov_text as an
already-downloaded subtitle, exactly like an internal SRT.

## The rules

All of them live in one place, `track_rules.py`, and both ends of the chain call
into it. The settings behind them are on the web page under **Media** (paths and
what the jobs may touch), **Languages** (the language rules and the subtitle
languages) and **Tracks** (subtitle types, forced mode, codec rules and the global
settings). The menu: Overview, Run, Statistics, Log, then the settings pages
Media, Languages, Tracks, Cleanup, Users, Automation, Connections, Security,
General, About.

**Audio.** Languages -> Audio has three cards, all stored in `audio_priority`
(the `orig` token first, last or absent) plus `audio_map`:

* *Prefer original language* - the title's own language first, then Your
  languages.
* *Prefer dubbed language* - Your languages first, the original language as the
  fallback when none of them is in the file.
* *Custom settings* - a table, one row per original language naming the language
  to hear instead ("English original -> audio is English"), then Your languages,
  then the original. The last row, *Any other original language*, decides every
  title without a row of its own: *Prefer original language* (`other:orig`),
  *Prefer dubbed language* (`other:list`) or *Plex settings decide*
  (`other:skip`), which writes nothing at all for that title - no flag in the
  file, no selection in Plex. An empty row means the same as `other:orig`, on
  both ends.

Within the chosen language the codec rules decide, then most channels, then the
order in the file. Commentary tracks are passed over, though the mkv side will
take one when it is the only audio there is. Both ends ask the same question,
`is_commentary()` in track_rules.py, which looks for "commentary" or
"kommentar" in either title field. If no language matches, the mkv side keeps
the track that is already default when there is exactly one and its codec is
not avoided, otherwise it runs the same ladder without the language step; the
Plex side leaves the file alone instead. When Radarr/Sonarr do not know the
original language, both ends skip the title: the listener drops the import
before the flag script starts, and the weekly batch does not collect it.

**Codec rules.** Tracks -> Codec rules gives a codec `prefer` or `avoid`.
They only decide between tracks in the *same* language, so the language order
above always wins first. Matching is on a substring of the codec name with
punctuation ignored, so `dts` covers every DTS flavour. `dtshd` is narrower but
not lossless-only: DTS-HD High Resolution normalises to a name containing
`dtshd` as well, so a `dtshd` rule catches that lossy flavour too. An avoided
codec is still taken when nothing else is left.
The full ladder is:
language -> codec rules -> most channels -> the order in the file.

**Subtitles.** Follow the language that will actually play - the audio track that
was picked, on both the mkv and the Plex side. With a dub picked, that is not the
title's own language:

* *Only when foreign language* (the reserved `follow` row) - no subtitle for any
  language you picked to hear under Audio; every other language gets the list
* *Always* - text in the language you hear for your own languages; every other
  language gets the list
* *Set per language* - one row per spoken language: *None*, the same language,
  another language (that one first, then the list), or any of them *forced only*
* *Never* (the reserved `never` row) - no subtitle at all, and a default or a
  saved selection that was already there is switched off on both ends. Forced
  lines in the list's languages are still picked unless Forced is unticked
  under Tracks, and a forced track may stay selected; a full one may not.

The list is *Subtitle language for foreign content* (`sub_langs`): the fallback
for every spoken language without a row, and for a choice the file lacks. The
forced mode under Tracks (`sub_forced_mode`) is *Only when there is no full
track* or *Also for languages you understand* - with the latter, a language that
gets no subtitle still gets its forced track.

**Tag rule.** Languages -> Tag settings binds one Radarr/Sonarr tag (read from
Radarr, Sonarr or both) to an Audio and a Subtitles section of its own; a title
carrying the tag follows those instead of Main settings. The fallback list is
shared. The tag is matched on folder names, so the container and the *arr apps
may see the library under different mount points. The mkv flags always follow
it; shared accounts follow it only while *Use tag rules for all users*
(Tracks -> Global setting, on by default) is on, and then it wins over the
account's own profile.

**Track types.** SDH, forced and image tracks are ranked by their setting under
Tracks -> Subtitle types: unticked is never, below Plain text is last resort, above
it is preferred. That ranking comes
**before** language, so a plain text track in your second language beats a forced
track in your first. Within the same rank the language order decides, then an
internal track before an external .srt, then the order in the file.

Only SubRip/SRT can become default by default. ASS/SSA is text-based but carries
styling and its own fonts that many Plex clients (Chromecast, Roku, some smart
TVs) cannot render - the text is then burned in, which forces transcoding.

## How it hangs together

On import, in this order:

1. Radarr/Sonarr import the file and post a webhook to webhook_listener.py
2. auto_set_mkv_defaultflags.sh sets the flags in the mkv file
3. The listener asks Plex to re-read that specific folder, so Plex is never left
   with a version of the file that was read before the flags were set
4. After the wait set by `after_scan_sec` (Automation, 60 seconds by default)
   auto_set_plex_defaults.py runs for the most recently added titles

Step 3 exists because Radarr/Sonarr often get Plex to scan before the flags are
set. Without that re-read Plex - and so everyone sharing the server - would see
the old flags.

When several episodes land close together they go into a queue. The
post-processing collects everything that arrives within `batch_sec` (15 seconds
by default, also on Automation) and runs one round, sized to the batch, instead
of throwing the later episodes away.

Once a week (Settings -> Automation -> Weekly check, default Sunday 06:00) the
listener's own scheduler thread runs schedule_helper.sh ->
auto_mkv_defaultflags_batch.py -> auto_set_mkv_defaultflags.sh per file. The
weekly run touches the mkv flags. When **Clean up first** is on
(`weekly_cleanup`) the scheduler runs the whole chain instead: trash files,
unwanted subs, mkv flags, Plex selections - in that order, in one thread. Note
what the last step means: that switch also adds a Plex pass over the whole
library, which rewrites the saved track for every title, not just the cleanups.

The two library jobs cannot start while a cleanup job is running. Asking for one
anyway puts it in a queue (the button says **Queue**) and it starts by itself as
soon as the cleanup is done. A queued job can be taken out of the queue again.

Manual: manual_unwanted_language_removal.py and manual_trashfiles_cleanup.py.
You start them from the Run page, which runs one `run_` wrapper each. Both are
a dry run until `--apply`, but neither claim people make about them is true:
they do run on their own when **Clean up first** is on, and they do delete
permanently when `--permanent` is added, which is what **Use quarantine** being
off selects.

## Where the original language lives

Bazarr can only read the audio tracks in the file. The notion of "the original
language is Swedish" exists only in Radarr and Sonarr, in the auto-tagging rule
that sets the tag `native-original`. The tag is the bridge that carries the
information on to Bazarr - without it a dubbed film with a Swedish audio track
would be treated as native-language original media.

No script in the chain reads the tag. The flag script only looks at which audio
tracks the file has; the Plex script asks Radarr and Sonarr for the original
language, never Plex. Plex is asked only for sections, metadata and streams.
That is why renaming the tag in Radarr/Sonarr needs no script changes, only the
field in the UI.

## The files

### track_rules.py
The rules, once. A normalised `Track` plus `pick_audio()`, `want_sub_langs()` and
`pick_subtitle()`, and the language table every other file builds on. Run as a
script it is the mkv side: it reads `mkvmerge -J` JSON on stdin and prints five
lines, the track number, the language, a note for the log, the mkvpropedit
arguments, and the arguments that describe the flags as they were, which is
what makes Undo work for the mkv side.

### webhook_listener.py
The container's main process. An HTTP server on 8899 that takes the webhook from
Radarr/Sonarr, checks the file is under the media root, runs the flag script,
asks Plex to re-read the folder, and queues the Plex pass. Also serves the web
page (through ui.py) and runs the weekly scheduler thread.

### ui.py and app/static/
The web page. `ui.py` is the backend - settings, the API, jobs, the queue, the
undo journal, statistics, log reading, sign-in, and the per-account Plex
profiles. The client lives in `app/static/app.html` (plus `login.html` and
`blocked.html`) and is served with an ETag, so an edited page is picked up
without a restart. Both live in the image, so changing them means a rebuild.

The page avoids polling where it can. On the pages that show something live (Overview, Run, Log,
Cleanup, Statistics) it keeps one `/api/events` stream open - a Server-Sent Events
response that says `changed` whenever a fingerprint of the job states, the undo
and the log sizes differs from a second earlier, and sends a comment every 30 s
so a proxy keeps the connection. The client then fetches `/api/status` as usual.
If the stream fails it falls back to a fetch every minute (every 5 s while a job
runs) for five minutes before trying the stream again. Hidden tabs, the other
pages and guest pages fetch nothing on their own; a Refresh link in the menu
fetches on demand.

`settings.json` is the source of everything. `settings_env.py` turns it into
the environment variables the scripts read, and the `run_` wrappers pick that
up with `eval "$(python3 /app/settings_env.py)"`. The rules, the alternative
set and both library roots are all in there, so a wrapper started by hand or
from cron behaves as the page does. The one thing only the page can supply is
`FLAGGARR_TAG_MAP`, the folder-to-tag file, because it has to be written out
before a script can read it.

### auto_set_mkv_defaultflags.sh
One file at a time. Reads the file with `mkvmerge -J`, asks track_rules.py what
to do, and writes the flags with mkvpropedit. `--dry-run` (or `-n`) only shows;
`--verbose` also prints the command; `--audio-only` and `--subs-only` write one
kind of flag, which is what the Automation switches use; `--original=CODE`
supplies the original language when the caller already knows it. Needs
mkvtoolnix and python3. No content is rewritten, only the file header changes,
so it is reversible.

### auto_mkv_defaultflags_batch.py
The whole library. Walks everything Radarr and Sonarr know about, looks up the
original language per title and calls the script above with the right
`--original`. Dry run by default; `--apply` writes; `--limit N` stops after N
files; `--script PATH` uses another flag script. `--quiet` drops the per-file
lines of a dry run and does nothing in an `--apply` run. Titles whose original
language Radarr and Sonarr do not know are skipped.

### schedule_helper.sh
What the weekly check runs: the batch script with `--apply`, with the settings
loaded first.

### auto_set_plex_defaults.py
The Plex side. Reads every title from Plex, works out the audio and subtitle
track with track_rules.py, and writes the choice back. This is the only thing
that reaches external .srt files and mp4, m4v and mov, and by default it runs
for mkv too. Dry run by default; `--apply` writes; `--recent N` limits it to the
most recently added, which is what the listener uses; `--audio-only` and
`--subs-only` follow the Automation switches; `--wait SEC` delays the start;
`--show-unmatched` lists the titles with no match in Radarr or Sonarr.

### manual_unwanted_language_removal.py
Moves aside loose .srt files in languages that do not belong next to tagged
content, by default English subtitles next to native-language originals. The
tag is matched against Bazarr, not Radarr or Sonarr, so the job exits straight
away without a Bazarr connection. Only touches titles carrying the tag, and
`--lang` must not be empty, and neither must `--tags`: with no tag there is
nothing for the job to look for, and it stops and says so rather than guessing
one. `--root` is the library root the quarantine layout is kept relative to.

### manual_trashfiles_cleanup.py
Finds external .srt files that should not be there, in the categories picked
under Cleanup: orphaned (no video with a matching name), redundant (the language
already exists inside the file as a subtitle track the Subtitle types settings
allow, plain text always, PGS/VobSub/ASS only when that type is not "never") and
files in a Subs folder next to the movie folder. Only orphaned is on by
default, so redundant files are left alone until you ask for them.

### run_trashfiles_cleanup.sh and run_unwanted_language_removal.sh
Thin wrappers the Cleanup page calls (buttons also reachable from Run). Exactly
`apply` means do it, `delete` means delete instead of quarantine, anything else
is a dry run. They load the settings, create the quarantine folder and append
to `config/log/manual_*.log`. The UI's **Use quarantine** switch under
Cleanup picks `apply` or `delete` for the button, and for the weekly chain
when **Clean up first** is also on.

## Worth knowing

Plex goes primarily by the preferred audio language in the account settings, and
only cares about the default flag when that language is missing from the file.
Everyone sharing the server is governed by their own Plex settings.

Bazarr saves external subtitles next to the media file and counts embedded
subtitles as already downloaded - but ignores embedded PGS, VobSub and ASS. If
there is no real SRT it will fetch an external one. The Tracks page shows a
red notice when Bazarr is set to ignore image formats.

## Quarantine

A `Removed Subs` folder beside the library, with one subfolder per job
(`flaggarr_trashfiles`, `flaggarr_unwanted_language`). Both the root and the two
names are settings. Each folder has its own `manifest.tsv` recording what was
moved from where, and the library's own folder structure is preserved under it.
The two manifests do not have the same shape, the trash one carries a category
column and the language one does not. The Cleanup page shows
what is in there, can restore it (reads the manifest, moves files back, drops
those rows) or empty a folder permanently. The **Use quarantine** switch there
decides whether the two cleanup jobs move files here at all, or delete them
outright - including on the scheduled weekly run.

## Accounts you share with

Everything above describes your own settings. The mkv flags are written into the
files and are the same for everyone, but the choice saved in Plex is per account.
The **Users** page lists every account the server is shared with (read from
plex.tv with the owner token; **Refresh users** reads it again) and each one can
be given a profile of its own: the language rules, the subtitle languages, the
subtitle types with the forced mode, and the codec rules. After the owner pass,
the Plex selection job runs once more per account, using that account's token -
with the account's profile when it has one, otherwise with the owner's settings,
so every account ends up with a selection. A card on Users is locked until
**Edit**; a profile the account saved itself says so on the card ("edited ..."),
and editing it then asks first, since the account is not told. **Reset to
global** drops a profile's own values so it follows the owner again, and **Reset all**
at the top of the page does that for every account after one confirmation. A card
reset either way says so ("reset by admin - 4 h ago") until someone saves it again.
Opening a card's Settings always starts with Audio, Subtitles and Tracks folded.

A guest can sign in themselves with **Continue with Plex** on the sign-in page
(a plex.tv PIN, no password ever reaches flaggarr). They then see only their own
page: a welcome screen with **Start**, then six steps - *Hearing* (hard of
hearing: subtitles on for every language, the hearing-impaired version first),
*Languages* (the languages understood, in order), *Audio* (original language or
dubbed), *Subtitles* (only when foreign, always, never), *Forced*
(`sub_forced_mode`: forced lines only when there is no full track, or also for
languages you understand - a "never" on subtitles still allows forced lines) and
*Finish*, which shows the summary with the cards *I'm happy with this* and
*Fine-tune*. A *Fine-tune* step sits between Subtitles and Forced for anyone who
opens the per-language tables. A link on the welcome screen, *Just use the
administrator's defaults*, keeps the profile off instead. Saving ends on a Thank
You page with *Start over*, *View summary* and *Fine-tune*; the latter opens the
full editor in five steps (Audio, Subtitles, Subtitle types, Codecs, Save) with
the answers pre-filled - a "Fine-tune" card on the Audio and Subtitles
steps opens the table straight from the questions. A guest who comes back lands on
a home screen that says the settings are saved, with the summary behind *View
summary* and the same three ways on. The profile starts out as the owner's
settings (`profile_view` in ui.py) until the guest says otherwise. A profile is
also only used once it is enabled: the answers are stored either way, but an
account whose profile is switched off follows the owner's settings. Two settings
under Tracks -> Global setting reach every account whatever a profile says:
**Never use image-based subtitles** forces PGS and VobSub to `never` for
everyone, owner included, and **Use tag rules for all users** makes the tag rule
apply to every account instead of the owner alone.

The owner's first visit shows a short **Welcome to flaggarr** dialog with *Don't
show this again*; General has a button that shows it again.

**Demo mode** on the Users page (owner only) runs the guest page in a sandbox
iframe: `previewShim` answers every API call from a fake account that starts on
the owner's settings, and nothing is written. The address follows the step -
`#demo/1` ... `#demo/7`, `#demo/summary`, `#demo/tune/1` ... `#demo/tune/5`,
`#demo/thanks` - so a step can be opened directly. The demo's Log out shows the
sign-in page (`/demo/login`) instead of signing the owner out.

## Statistics

Three tabs, one per thing flaggarr does. **Imports**: the numbers from the listener log
(flagged, last 7 and 30 days, errors, Plex selections, rescans), flagged per day, a
Movies and a Series card, the languages seen, and a searchable list of the newest
imports with an OK/Error pill, the language and where it came from. **Library**: the
state of the whole library as the last run of the Plex job measured it (settled, changed
or would change, out of reach) plus the Radarr/Sonarr counts, a card per library job with
runs, last run and next run, and what changed by kind, by Plex library and by codec.
**Cleanup**: the quarantine, the two cleanup jobs and a row per quarantine folder. The
numbers behind Library are read back from the job's own log (`dry_summary()`,
`weekly_summary()`), so they are as old as that run - the card says when.

## Check file

An ERROR line about a file on the Run or Log page (typically `mkvpropedit: The
file ... could not be opened`) has a **Check file** button. It shows the container
account's access to the file and its folder and what mkvtoolnix makes of the
file, and can run the flag script on that one file again - a real run with its
own undo journal. Only files under the library folder can be checked.

## Undo

Every real run of the two library jobs writes a journal into `config/undo`, one
line per change as it is made, so a run that is killed half way leaves a journal
covering exactly what it got through. One file per run and account, named
`<run>_<job>[_u<account>].tsv|jsonl`. The mkv side stores the mkvpropedit
arguments that describe the flags as they were, the Plex side the stream ids that
were selected. General -> Undo lists the runs that changed something and puts one
whole run back, using the right token per account. Journals of the last twelve
runs are kept, a run can only be put back once, and imports are not journalled.

## Logs

| File | Written by |
|---|---|
| `log/webhook_listener.log` and `.err` | the listener |
| `log/manual_trashfiles_cleanup.log` | run_trashfiles_cleanup.sh |
| `log/manual_unwanted_language_removal.log` | run_unwanted_language_removal.sh |
| `log/ui_weekly.log` and `ui_plex.log` | the weekly check / the Plex pass when started from the web page |

The listener log is trimmed to `log_max_lines` (20 000) once it has grown half
again as long. The job logs are capped by size instead (`job_log_max_mb`, 2 MB),
trimmed at a run boundary so no run is left
half there.

The Log page has two tabs. **Actions** is what was done to the media, **System**
is the listener talking about itself. **Clear** empties the tab you are looking
at: its own files, and the lines of that kind out of the listener log. The other
tab keeps everything it shows.

## Known gaps

* A number of loose .srt files have names that do not match their video file.
  Plex never sees them. manual_trashfiles_cleanup.py lists them.
