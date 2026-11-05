# Script workspace

The Scripts view imports COBRA-style UTF-8 CSV files into the app and checks them
against radio discovery and continuity snapshots. It does not upload, arm or run
scripts. The radio's diagnostic command allowlist is unchanged.

Use **Load script** to select a file with the platform file picker. Canceling
keeps the current script. A failed import clears the previous document/report so
an earlier successful check cannot be mistaken for the new file. Imported data
stays in memory across navigation; restarting the application requires reloading.

The collapsed **How script checks work** section contains the channel mapping,
saved-reading guidance, and local import limitations. When disconnected, a single
offline card provides the shortcut back to the radio connection.

**Control layout preview** shows Script and Manual fire layouts. Start, Pause,
Stop, and Fire are disabled visual placeholders. Switching layouts is local UI
state only; this widget has no transport or command callbacks.

The Manual fire preview includes a group selector and a read-only cue overview.
All discovered modules in that group are shown, including duplicate groups and
modules not seen in the latest scan. Tiles reuse the module view's fixed size,
continuity colors and measurement details. Saved snapshots remain visible when
offline, with measurement warnings and missing readings identified. **Open module
view** navigates to that group for discovery and continuity checks. Cue tiles do
not select outputs or send commands; Fire remains disabled. Selecting a tile
and pressing **Preview cue** blinks that tile three times, then retains its outline
and a **Last preview** label. A new preview replaces the old marker. Reduced-motion
settings skip blinking and show the marker immediately. This is local visual
feedback and does not indicate a firing command or acknowledgement.

**Open offline simulation** opens a separate dialog containing four invented demo
cues. Its simulated arm switch enables **Simulate fire** only after a demo cue
is selected. Pressing it blinks and labels that dummy cue; selecting another and
pressing again moves the marker. Disarming disables the simulated action. Closing
the dialog cancels its timers and resets state; reopening starts disarmed. This
widget has no radio/session imports, real module data, or output callbacks.
Within the dialog, **Simulator settings → Simulated arm source** selects Software
or Hardware arm switch layouts. Hardware is a dummy input controlled inside the
dialog, not a GPIO reading. Changing sources resets simulated arming and cue
selection. The choice lasts only for that dialog session and does not configure
the radio or persist an application-wide arming preference.

## Simulated upload

After importing a script, expand **Simulated upload**. **Use demo modules** is on
by default and invents one matching module per script channel with connected
demo readings. Turn it off to validate against the current discovered snapshots.
Import errors, missing/duplicate groups, and invalid continuity block the demo.
The existing checker provides validation; this panel has no radio transport.

**Demo result** can make all targets succeed, or inject a timeout/rejection into
the first target. **Simulate upload** displays a timer-driven result per matched
module and the number of cue events. It does not encode or transmit events.
**Cancel simulation** marks unfinished targets cancelled. After an injected
failure, select **All succeed** and use **Retry failed simulation** to retry only
the failed demo targets while retaining completed ones. A new simulation resets
all demo results. Replacing/unloading the script disposes timers and results.
Results refer to snapshots captured at start; they never set module script IDs,
change Loaded status, or confirm hardware readiness.

Use **Check connected modules** to run discovery and an all-module continuity
check. Results also update when discovery, continuity or radio connection changes
elsewhere in the app. Loading alone sends no radio queries. Opening the app's
script picker preserves the USB session through its pause/resume cycle, including
selection, cancellation, and picker errors. The exemption ends after the picker
returns and the activity resumes. Ordinary backgrounding outside that picker
cycle disconnects the radio. Physical USB loss or a failed heartbeat still ends
the session normally. If Android aborts a transport during the picker (notably
the emulator's TCP relay when backgrounded), the app makes one reconnect attempt
to the previously connected radio after both result and resume arrive. A recovered
connection starts a fresh session; discover modules and refresh continuity again.
If recovery fails, use **Open radio connection** to reconnect manually.

Tap a finding under **Check results** to open the Modules view. The referenced
module or group is expanded, previous search/status filters are cleared, and
the referenced cue is highlighted. Duplicate groups show all matching modules;
missing hardware is identified instead of selecting an unrelated module.
Use **Show all modules** to clear that focus. Findings without a hardware target
open the module overview.

Open **Timeline preview** to inspect scheduled times without sending commands.
The slider snaps to the closest event time; previous/next buttons move between
distinct timestamps, including simultaneous cues. Each cue shows its current
check status and opens the corresponding module/group when tapped. Large groups
are paged 25 cues at a time. The preview orders timestamps for inspection without
changing the imported script or suppressing out-of-order validation errors.

While a script is loaded, connected cues without an assignment in that script
are orange and labeled **Not in script**. Assignments use group/channel and cue,
so matching by module UID is not required. Unloading the script restores normal
continuity colors. Connected/open/short counts remain hardware counts and stay
visible in each module's header when collapsed.
An orange **Not in script** count follows **Short** in both overall and module
summaries. It counts only connected cues absent from the loaded script, displays
zero when none are unused, and disappears when the script is unloaded.

## Format

This follows `load_cobra_script` and `parse_script_time_ms` in `main/master_main.py`:
script channel maps to module group, not module UID. Cues start at 1.

```csv
#Trigger Channel,#Trigger Button,#Script Name
1,1,Example show
#Event Time,#Channel,#Cue,#Description
00:00.000,1,9,First example
00:01.000,1,10,"Description, with comma"
END
```

Trigger metadata is optional. The event header and its first four columns are
required. Additional columns are preserved in the file but are not interpreted.
Quoted commas, quoted newlines, escaped double quotes, CRLF and a UTF-8 BOM are
supported. Times accept seconds, `MM:SS.mmm`, or `HH:MM:SS.mmm`, optionally ending
in `s`. Fraction precision is at most three digits and time must fit unsigned
32-bit milliseconds. Negative or invalid times are errors, not zero-time events.

The checker reports malformed rows, out-of-order events, repeated channel/cue
assignments and data after END. It does not silently sort or repair a script.
Rows with both channel and cue blank are counted as unsupported auxiliary/DMX
rows and produce a warning. Missing just one field is an error. Files are limited
to 1 MiB and 10,000 valid cue events. Events are paged 25 at a time and findings
are paged 50 at a time. Saved continuity readings do not expire automatically;
each checked event shows its measurement timestamp. Refresh explicitly to obtain
new readings.

## Hardware checks

- Radio must be connected. Saved data cannot pass an offline check.
- Each referenced channel must match exactly one module seen in the latest scan.
  Missing groups, groups not seen in the scan and duplicate groups are errors.
- Cue numbers must fit that module's reported cue count.
- Continuity must have a valid saved measurement; future-dated, failed,
  unmeasured or faulted snapshots cannot pass. Elapsed time alone does not turn
  a reading into an error or remove a module.
- Open, short, invalid and uncalibrated raw readings are errors for referenced cues.
- Connected cues in saved snapshots that are not referenced by the script are
  reported as warnings. These warnings do not imply current continuity.

Results describe snapshots. Even an error-free result is not remote arm
confirmation, authorization to fire or validation of timing execution, audio/DMX,
script upload capacity or firmware compatibility.

The repository includes `examples/scripts/interface-check.csv` for a manual import
test. It references group 1 cues 9, 10 and 15; it is not an executable show approval.

File import uses Flutter's `file_selector` plugin:
https://pub.dev/packages/file_selector . Windows/iOS/macOS file selection is wired
through the plugin but has not been device-tested here. macOS includes the
read-only user-selected-file entitlement; the radio transports remain Android-only.

## Validation

The Android tablet emulator completed native CSV selection, discovery and
continuity against the real radio through the local development relay. The example
reported group 1 cue 15 as shorted, plus four connected cues unused by the script.
Saved readings now remain available beyond 30 seconds. Eighty-eight Flutter tests pass,
including CSV edge cases, offline/ambiguous hardware checks, saved readings older
than 30 seconds and two hours, import cancellation/failure, changing
findings and concurrent heartbeat/query writes on the relay.
