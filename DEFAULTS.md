# flaggarr - defaults

Every setting flaggarr has, with the value a fresh install starts from and what
it does. Almost all of it is set from the web page (port 8899), where a
**Default** button appears beside a field you have changed and puts that value
back. The page a setting lives on is given in brackets, and the few settings
with no field anywhere say so in their row.

The values below are the ones in `app/ui.py` (`DEFAULTS`). `app/settings_env.py`
carries the same values for scripts started outside the page, so a wrapper run
by hand or from cron sees what the page sees. Change them in those two files if
you fork the project and want other factory settings; change them on the page if
you just want other settings.

Language codes are ISO 639-2, space separated (`swe eng`). `orig` stands for the
title's own language.

## Template: the four things you always set

| Where | What | Default | Set it to |
|---|---|---|---|
| `compose.yaml` | media volume | `/volume1/PlexMedia:/volume1/PlexMedia` | your library, **same path inside and outside** |
| `compose.yaml` | `TZ` | `Europe/Stockholm` | your time zone |
| `compose.yaml` | `PUID` / `PGID` | commented out (root) | your own user, the one your other *arr containers run as (read/write on the library); `id -u` / `id -g`, and on a Synology rarely 1000 |
| Connections | Plex, Radarr, Sonarr, Bazarr | empty | address + token/API key for each |

Then Media -> Languages: which languages you understand, and in which order.
Everything else can stay as it is.

## Overview / Automation - what runs [Automation]

| Setting | Default | Meaning |
|---|---|---|
| `listener` | on | accept imports from Radarr/Sonarr (the webhook) |
| `mkv_flags_audio` | on | write the default AUDIO flag into the mkv (mkvpropedit) |
| `mkv_flags_subs` | on | write the default SUBTITLE flag into the mkv |
| `plex_rescan` | on | step 2: ask Plex to re-read the folder after flagging |
| `plex_audio` | on | step 3a: correct the audio pick Plex has saved |
| `plex_subs` | on | step 3b: correct the subtitle pick Plex has saved |
| `batch_sec` | 15 | imports arriving within this many seconds are handled as one batch |
| `after_scan_sec` | 60 | how long to wait for Plex after the rescan |
| `recent_min` | 5 | the Plex pick looks at at least this many "recently added" items |
| `margin` | 5 | added to the batch size when deciding how many recent items to check |
| `weekly_on` | on | a weekly pass over the whole library, run by the container itself |
| `weekly_day` | 6 | 0 = Monday ... 6 = Sunday |
| `weekly_time` | `06:00` | when the weekly pass starts |
| `weekly_cleanup` | off | run the cleanups before the weekly flag pass, and a Plex pass over the whole library after it. That last step rewrites the saved track for every title, so this switch does considerably more than its name says |
| `import_url` | empty | the webhook address to paste into Radarr/Sonarr. Written by the program from the address of the last direct visit, shown read-only with a Copy button, and not something you set |

## Languages [Languages]

| Setting | Default | Meaning |
|---|---|---|
| `audio_priority` | `orig eng` | audio order. `orig` first = original language, your languages as fallback; your languages first = prefer dubbed |
| `sub_rules` | `eng:none` | per spoken language: `none` = understood, no subtitles; `same` = text in that language; a 3-letter code = read that language's text instead; any of those plus `+forced` = forced tracks only. Three reserved rows carry no value: `follow` = every language in the audio list is understood, `never` = no subtitles at all, `skip` = write nothing here and leave it to Plex |
| `sub_langs` | `eng` | subtitles for any other spoken language: first in this list that exists |
| `audio_map` | empty | per original language, which audio to hear instead (`jpn:eng`), plus `other:orig` (also what no `other` means) / `other:list` / `other:skip` for the rest. Only consulted when `orig` is **not** in `audio_priority`, so with the factory audio order it has no effect at all |
| `audio_codec_rules` | empty | per-codec rules, e.g. `truehd:prefer dtshd:avoid` |
| `native_langs` / `same_sub_langs` | `eng` / empty | derived from `sub_rules` and rewritten whenever it is saved, so there is no field and no point editing them. The scripts read these |
| `alt_tag` | empty | titles carrying this Radarr/Sonarr tag use the alternative set below |
| `alt_tag_radarr` / `alt_tag_sonarr` | on / on | where the tag is read from |
| `alt_audio_priority` | `eng orig` | the alternative audio order (dubbed animation, say) |
| `alt_sub_rules` | `eng:none` | the alternative subtitle rules; `sub_langs` is shared |
| `alt_audio_map` | empty | the alternative per-language audio table, inert unless `orig` is out of `alt_audio_priority`, as above |
| `alt_native_langs` / `alt_same_sub_langs` | derived | the alternative set's pair of the two above, written when `alt_sub_rules` is saved |
| `tag_all_users` | on | the tag rule also applies to accounts with their own profile |

## Tracks [Tracks]

| Setting | Default | Meaning |
|---|---|---|
| `sub_track_order` | `text forced` | which subtitle types may be picked, and in what order. Ticked rows only. `bitmap` is an alias that stands for all three of pgs, vobsub and ass |
| `sub_track_display_order` | empty | the full row order including unticked rows, so ticking one never moves it. Written by the page, nothing to set |
| `sub_sdh` | `never` | derived from the order: `never` / `last` / `first` - hearing-impaired subtitles |
| `sub_forced` | `last` | derived: forced subtitles (only the lines spoken in another language) |
| `sub_forced_mode` | `nofull` | `nofull` = forced only when there is no full subtitle; `native` = also for a language you understand |
| `sub_pgs` / `sub_vobsub` | `never` | picture subtitles (Blu-ray, DVD) - off by default |
| `sub_ass` | `never` | styled ASS/SSA - off by default, and governed only by this key. The image switch below does not touch it |
| `mkv_plex_select` | on | mkv: the flag in the file also sets the Plex pick |
| `mp4_plex_select` / `m4v_plex_select` | on / on | mp4/m4v: no flags can be written, but the Plex pick still runs |
| `mov_plex_select` | on | mov: no flags either, but the Plex pick still runs |
| `users_no_image_subs` | off | on, PGS and VobSub are forced to `never` for **every** run, yours included, whatever a profile says. The name understates it |

## Cleanup [Cleanup]

| Setting | Default | Meaning |
|---|---|---|
| `media_root` | `/volume1/PlexMedia` | the path an import must be under, or it is dropped. No field on the page: the library walks use the detected media mount instead, and fall back to this only when nothing looks like one |
| `movies_root` / `series_root` | empty | Radarr's / Sonarr's library path. Empty falls back to the detected mounts first, and to `media_root` only if there are none. These reach the cleanup job from the page alone, so a wrapper run by hand ignores them |
| `use_quarantine` | on | off: the cleanup jobs delete straight away |
| `quarantine` | `<media root>/Removed Subs` | where removed files go, with a manifest so they can be restored. The Default button offers it beside your own library, so the literal value only matches an install mounted at `/volume1/PlexMedia` |
| `quarantine_trash` | `flaggarr_trashfiles` | subfolder for Remove trash files |
| `quarantine_langs` | `flaggarr_unwanted_language` | subfolder for Remove unwanted subs |
| `trash_categories` | `orphaned` | which Remove trash files categories the job includes |
| `unwanted_langs` | `eng` | languages Remove unwanted subs removes |
| `native_tag` | empty | the tag Remove unwanted subs looks for, matched against **Bazarr**, not Radarr or Sonarr. Empty means the job has nothing to look for and stops with a message saying so, and it also stops without a Bazarr connection |

## Security [Security]

There is no state in which flaggarr answers without a sign-in. Until an
account exists the page serves one thing, the form that makes it, and every
other call is refused; afterwards the password applies. The webhook from
Radarr and Sonarr is not part of the page and keeps working throughout.

No secret is served to the page even when signed in: the connection tokens,
the password hash and the cookie-signing key are all left out of what the
settings call returns.

| Setting | Default | Meaning |
|---|---|---|
| `auth_lan` | on | the password is required from everywhere. Turn it off to exempt the home network, which is judged on the address the request arrived from, not on a header: private ranges, loopback and overlay networks count as local, a public address does not, so remote still needs the password. Left on because Docker's userland proxy can make every request look local |
| `signout_on` | on | sign a browser out automatically |
| `signout_min` | 60 | after this many minutes from sign-in, when `signout_on` is set |
| `brute_on` | off | lock the address after too many failed sign-ins |
| `brute_tries` | 5 | failed attempts before the lockout |
| `brute_reset_days` | 7 | a wrong sign-in is forgotten this many days after the last one |

## General [no field on the page]

The two log limits are edited in `config/settings.json`; `quickstart_done` is set by
the first-run wizard. They are not the only settings without a field:
`media_root`, `native_langs`, `same_sub_langs`, `sub_track_display_order` and
`import_url` have none either, each noted in its own row above.

| Setting | Default | Meaning |
|---|---|---|
| `log_max_lines` | 20000 | the listener log, including its error log, is trimmed to this once it grows 1.5x. Anything under 20 switches trimming off |
| `job_log_max_mb` | 2 | a job log is capped by size |
| `quickstart_done` | off | the first-run wizard is shown until finished or skipped |

## Per-user profiles [Users]

A Plex account that signs in on the page gets its own profile with the same
keys as Languages and Tracks: `audio_priority`, `audio_map`,
`sub_rules`, `sub_langs`, `sub_track_order`, `sub_forced_mode`,
`audio_codec_rules`. Alongside them sits `enabled`, which decides whether the
profile is used at all: off, the account follows your settings no matter how
completely its answers are filled in. A profile starts out following your
settings; the questions on the guest page fill it in, and the card also stores
the account name and when it was last edited or reset. Profiles live in
`config/settings.json` under `user_profiles` - that file is gitignored and
holds tokens too, so it never goes into the repo.

## Environment (compose.yaml)

| Variable | Default | Meaning |
|---|---|---|
| `TZ` | `Europe/Stockholm` | time zone for schedules and logs |
| `VERSION` (build arg) | `dev` | stamped onto the image as OCI metadata, so Container Manager and `docker inspect` can report the version instead of a hand-typed tag. The version itself lives in `app/ui.py`; `compose.example.yaml` deliberately pins no number, because a pinned one would go stale and lie. Pass it at build time to stamp the real value |
| `PUID` / `PGID` | commented out (root) | the user and group the program runs as; set them to the same user your other *arr containers use; needs read/write on the library, and gets `/config` on start. Left out it runs as root, which always works; a wrong UID does not, so look yours up rather than guessing. 1000 is a Linux desktop convention and usually wrong on a Synology, where accounts start at 1026 and the group is `users` (100) |
| port | `8899` | the web page |
| `./config:/config` | - | settings, logs, backups; created on first run |
| `./README.md`, `./REFERENCE.md` | - | mounted read-only so About can offer them as downloads. Leave them out and that row stops working |
| `DOCS_DIR` | `/docs` | where those two files are looked for inside the container |

Set `PUID`/`PGID` to a user with read/write on the library: mkvpropedit writes
into the files, and the cleanup jobs move them. Without them the container runs
as root, which works everywhere but is more than it needs.
