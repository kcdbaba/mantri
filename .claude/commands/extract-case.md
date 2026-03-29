# extract-case

Extract a time-windowed snippet from WhatsApp chat exports, annotate images/PDFs with vision,
and write a formatted `threads.txt` ready for eval.

Usage:
  /extract-case --case tests/evals/<case_dir>
  /extract-case --new tests/evals/<case_dir>
  /extract-case --start "3/2/2026, 20:00" --end "3/18/2026, 23:59" --chats data/raw_chats/dir1 data/raw_chats/dir2 --output tests/evals/adhoc/threads.txt

Arguments: $ARGUMENTS

---

## Instructions

Parse `$ARGUMENTS` for one of three modes:

### Mode A — `--new <case_dir>`

Create a blank case:
1. Create `<case_dir>/` if it doesn't exist.
2. Write `<case_dir>/metadata.json` with this template (do not overwrite if file already exists):

```json
{
  "id": "",
  "name": "",
  "framework": "",
  "level": "",
  "sprint": "S2",
  "data_source": "real-incomplete",
  "description": "",
  "chat_inputs": {
    "start": "",
    "end": "",
    "chats": [
      {"path": "data/raw_chats/full_chat_logs/...", "label": "..."}
    ]
  },
  "completeness": {
    "complete": false,
    "missing": []
  },
  "expected_output": "",
  "pass_criteria": "",
  "notes": ""
}
```

3. Tell the user to fill in the fields and re-run with `--case <case_dir>`.

---

### Mode B — `--case <case_dir>` (case mode)

1. Read `<case_dir>/metadata.json`.
2. Extract `chat_inputs.start`, `chat_inputs.end`, and `chat_inputs.chats` (list of `{path, label}`).
3. Proceed with extraction (see Extraction steps below), writing output to `<case_dir>/threads.txt`.

---

### Mode C — `--start`, `--end`, `--chats`, `[--output]` (ad-hoc mode)

Use the provided start datetime, end datetime, and chat directory paths.
Output path defaults to `tests/evals/adhoc/threads.txt` if `--output` not given.
Labels default to the directory name.

---

## Extraction steps (Modes B and C)

For each chat directory:

1. Find the `.txt` file in the directory (WhatsApp export). Skip directories with no `.txt` file.
2. Parse the `.txt` file into messages. WhatsApp export format:
   ```
   DD/MM/YY, HH:MM - Sender Name: message text
   ```
   Multi-line messages: lines that don't start with a timestamp+dash are continuations of the previous message.
3. Filter messages to the time window (start ≤ timestamp ≤ end). Try both `MM/DD/YY` and `DD/MM/YY` datetime formats.
4. For each message in the window:
   - If the message contains an attachment (line ending in `(file attached)`), note the filename.
   - Check if the file exists in the chat directory. Supported: `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.pdf`.
   - If the file exists: read it and annotate it using your vision capability. Produce a one-line annotation:
     `[IMAGE:<type>] <chat_annotation> | implied_event: <implied_event>`
     Types: handwritten_note, proforma_invoice, payment_confirmation, payment_ledger, courier_note, order_list, product_screenshot, product_photo, delivery_challan, other
   - If the file does not exist: replace with `[IMAGE:not_in_export — <filename>]`.
   - Replace the attachment line in the message content with the annotation.
5. Format the thread as:
   ```
   === THREAD <N>: <label> ===
   [<timestamp>] <sender>: <content>
   ...
   ```
6. Join all threads with a blank line between them.
7. Write to the output path (`threads.txt`).
8. Print a summary: threads processed, messages per thread, images annotated.
