# Telegram -> Manual Upload Path Integration

## Requirement

Telegram file intake must be treated the same as a manual upload from HOST.

That means:

Telegram attachment
-> saved on NODE
-> represented in UI like uploaded file
-> queued into the same manual ingest path
-> same source type choices
-> same truth metrics
-> same truth gate
-> no auto-promotion

## Current proven path

HOST upload already works:

HOST file picker
-> /home/rick/incoming/uploads/
-> Source path auto-filled
-> Queue manual ingest
-> worker runs manual_ingest.py
-> file staged under /home/rick/NODE/staging/manual/
-> promoted = false

Telegram must follow the same path semantics.

## Telegram storage path

Save Telegram-uploaded files under:

/home/rick/incoming/telegram/YYYYMMDD_HHMMSS_<safe_filename>

Use safe filename normalization.
Do not allow path traversal.
Do not auto-execute files.
Do not auto-promote files.

## Telegram UX / behavior

When Rick sends a document/file to the Telegram bot:

1. Download attachment to:
   /home/rick/incoming/telegram/

2. Record metadata:
   - timestamp
   - telegram file name
   - saved path
   - size if available
   - sender/chat id if needed internally
   - default source_type = unknown unless detected

3. Make it available in the same intake flow as manual uploads.

## Required equivalence with HOST upload

Telegram-uploaded files must be usable exactly like a HOST-uploaded file:

- selectable from UI
- fills Source path
- supports type:
    notes
    paper
    transcript
    code
    documentation
    web_capture
    unknown
- supports mode:
    stage_only
    truth_gate
    corpus_candidate
- queues manual_ingest.py
- lands in /home/rick/NODE/staging/manual/
- subject to same truth metrics
- not auto-promoted

## UI requirement

Manual Ingest tab should have a Telegram intake section.

Display:
- recent Telegram files from /home/rick/incoming/telegram/
- click one to fill Source path
- optionally show file name and timestamp

This is not a separate truth path.
It is the same manual ingest path.

## Backend requirement

In ui_server.py add or keep:
- GET /api/telegram-files
  returns recent files from /home/rick/incoming/telegram/

If not already complete, ensure:
- results are sorted newest first
- clicking a Telegram file fills Source path
- user still chooses source type and mode before queueing

## Queue requirement

Queue command remains manual_ingest.py, e.g.:

python3 manual_ingest.py --path "<telegram_saved_path>" --mode "<mode>" --type "<type>" --tags "<tags>"

Do not create a separate ingest doctrine for Telegram files.

## Truth doctrine unchanged

Rick approves system action and scope.
Rick is not the truth standard.

Promotion requires:
- two verified supporting sources
OR
- two indirectly supporting facts

Pipeline remains:
acquisition != evidence != sufficiency != promotion

## Implementation target

Patch:
  /home/rick/NODE/ui_server.py

Possibly patch Telegram intake handler wherever Telegram downloads are currently handled.

## Acceptance test

1. Send a test file to Telegram bot.
2. Verify file lands in:
   /home/rick/incoming/telegram/
3. Open Manual Ingest UI.
4. Telegram section lists the file.
5. Click file -> Source path fills.
6. Set type = paper or notes.
7. Queue manual ingest.
8. Run worker if needed.
9. Verify staged file appears under:
   /home/rick/NODE/staging/manual/
10. Verify promoted = false.

