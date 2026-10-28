# Pi runtime contracts

Captured by the bounded Tempus investigator on 2026-10-24, pi 1.4.0 and
Herdr 0.9.4. Files are byte copies from the accepted issue #71 runtime evidence:
`skills/herdr-teamlead/tests/fixtures/pi/start.json` from the lead's pi-start capture;
`skills/herdr-teamlead/tests/fixtures/pi/help.json`, `skills/herdr-teamlead/tests/fixtures/pi/version.json`,
`skills/herdr-teamlead/tests/fixtures/pi/process-info.json`, `skills/herdr-teamlead/tests/fixtures/pi/composer.json`,
`skills/herdr-teamlead/tests/fixtures/pi/native-accept.jsonl`, `skills/herdr-teamlead/tests/fixtures/pi/wait-accept.json`
and `skills/herdr-teamlead/tests/fixtures/pi/wait-reject.json` from investigator captures. JSON command envelopes
preserve command, exit code, stdout and stderr. The native transcript already
omits system context and contains only the harmless evidence turn.

The developer profile adds `edit` to the original captured `read,bash,write`
launch: pi 1.4.0's captured help lists it as a built-in find/replace tool.
Tests synthesize the new requested launch argv while preserving the historical
capture bytes; no live developer launch or model-quality result is claimed.

The old positive wait receipt has exit 4; it demonstrates the original wrapped
marker refusal, not a historical successful delivery. Tests replay the native
source through the new source-bound path, substituting temporary report paths.
The original file's first user prompt and parent chain stay the binding oracle.

`Apple M5 Max` and `68719476736` are live-observed host values. `Apple M1` is
the brief-supplied target string, not a live M1 measurement. The 32 GiB boundary
(`34359738368` bytes) is the operator-selected default, not a live hardware
measurement. Byte shortfalls,
changed PIDs/profiles, remote endpoints and malformed transcripts in the tests
are explicitly synthetic negative cases. Tests read no original user path.
