# Human Upload Routes Correction

## Core correction

Human-provided documents enter the NODE pipeline through exactly two human upload routes:

1. HOST upload
2. Telegram upload / Telegram attachment intake

The previous focus on "Search NODE files" was incomplete and misleading.

## Correct upload model

### Route A — HOST upload

The UI must provide a real browser file picker.

Label:

Upload from HOST

Behavior:

- User clicks Upload from HOST.
- Browser opens OS file picker on HOST.
- Selected file uploads through the existing HOST-to-NODE tunnel.
- NODE stores the uploaded file under:

  /home/rick/incoming/uploads/YYYYMMDD_HHMMSS_<safe_filename>

- UI auto-fills Source path with the uploaded NODE path.
- User chooses type:

  notes
  paper
  transcript
  code
  documentation
  web_capture
  unknown

- User clicks Queue manual ingest.
- The file enters the same truth-gate pipeline as other material.

Endpoint:

POST /api/upload-file

Required response:

{
  "ok": true,
  "path": "/home/rick/incoming/uploads/...",
  "size": 12345,
  "message": "uploaded to NODE"
}

### Route B — Telegram upload

Telegram must support document/photo/file attachment intake.

Behavior:

- Rick sends a file/document to the Telegram bot.
- Telegram handler downloads the attachment to NODE.
- NODE stores the uploaded file under:

  /home/rick/incoming/telegram/YYYYMMDD_HHMMSS_<safe_filename>

- A queue entry is created for manual ingest or review.
- Telegram replies with a compact confirmation showing:
  - saved path
  - file size
  - detected type if possible
  - next action needed if type is ambiguous

Telegram must notify Rick only when attention is needed.

## Manual Ingest UI changes

The Manual Ingest tab should not imply that NODE search is the primary human upload path.

Required visible controls:

1. Upload from HOST
   - file input / browser file picker
   - uploads to /home/rick/incoming/uploads/
   - auto-fills Source path

2. Telegram upload note
   - text explaining:
     "You can also send files to the Telegram bot. They will be staged under /home/rick/incoming/telegram/."

3. Source path
   - shows the uploaded/staged NODE path
   - may still accept a manually typed NODE path

4. Source type
   - notes
   - paper
   - transcript
   - code
   - documentation
   - web_capture
   - unknown

5. Tags

6. Queue manual ingest

Optional secondary control:

Search existing NODE files

This is only for files already on NODE.
It is not the upload path.
It should be clearly labeled secondary.

## Acceptance / truth gate

HOST upload and Telegram upload both enter the same truth-gate path:

uploaded file
-> staging/manual or staging/telegram
-> truth gate
-> accepted/elevated/rejected/needs_review

Neither HOST upload nor Telegram upload auto-promotes.

Rick approves system action and scope.
Rick is not the truth standard.

Promotion requires:

- two verified supporting sources

OR

- two indirectly supporting facts

Pipeline remains:

acquisition != evidence != sufficiency != promotion

## Required implementation

In /home/rick/NODE/ui_server.py:

Add:

POST /api/upload-file

Use safe filename handling.
Do not allow path traversal.
Do not write outside /home/rick/incoming/uploads.
Do not execute uploaded files.
Do not auto-promote uploaded files.

In Telegram bridge/intake code:

Add or preserve:

Telegram attachment download
-> /home/rick/incoming/telegram/
-> manual ingest queue/review state

Do not commit secrets.
Do not log Telegram token.
Do not upload to GitHub.
