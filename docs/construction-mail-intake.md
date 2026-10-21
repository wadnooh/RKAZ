# Construction mail intake — reportrek

Prepared on 2026-10-20. Source mailbox selection updated on 2026-10-21.

## Live configuration

- Private native project 10: الإنشاءات — المعاملات الواردة.
- Native task-list action 1353 and its Projects menu.
- Initial reviewed-from-document draft task 199, titled `A26-118 — طلب 261153514 — النرجس`.
- Original subject, file reference, service request number, extracted site, intake key and review status fields.
- Unique company/intake-key index prevents repeated ingestion of the same supplied key. Future connector must provide a stable message/file identity; this is not an implemented mailbox importer.
- Initial description identifies manual document analysis during setup, not a fetched email or an automatic PDF extraction.
- Scoped task text-extraction automation and admin contextual action, limited to project 10. Explicit service-request labels are parsed, uncertain/conflicting values remain pending, and reviewed records are skipped.
- Native project mail alias was initially unset. On 2026-10-20 the user selected `rtcschame@gmail.com`; the project's receiving alias was saved as `construction-intake@reportrek.odoo.com`. Project visibility remains Team members only. No mailbox credentials, fetch jobs, outgoing messages or external AI calls were configured.

- On 2026-10-21 Gmail identity verification and destination confirmation completed. Gmail forwarding confirmation was received natively as task 200 in project 10. This proves alias delivery of the confirmation, not a real construction PDF end-to-end test.
- With explicit user confirmation for new matching messages and their attachments, saved and visibly verified exactly one Gmail filter: `from:(rtcschame@gmail.com)` -> `construction-intake@reportrek.odoo.com`. No archive/delete action or historical-message application was selected. Proof: `outputs/gmail_odoo_filter_enabled_20261021.png`.

## Pending

- Source is Gmail `rtcschame@gmail.com`, superseding intermediate Titan `rtcscheme@rtcco.org`. The user explicitly selected that same address as the sender filter. Do not broaden to other senders or mailbox-wide forwarding. A real new matching construction message with attachment has not yet been verified end-to-end.
- Arabic PDF layout/OCR and multimodal extraction are not connected. The supplied five-page mixed PDF was visually reviewed; text extraction alone is unreliable.
- Email Message-ID and attachment fingerprint deduplication, authorized sender filters and retry/dead-letter handling belong to the future connector.
- The user approved uploading the full source PDF including ownership/identity pages to this private task. Upload failed because the ChatGPT Chrome extension lacks Allow access to file URLs. No attachment upload succeeded. Resume with the normal file chooser after the user enables that permission, or have the user attach the file manually.
- No construction-stage approval or accounting/stock posting is performed by this intake extraction.

## Source and tests

`tools/odoo_construction_mail.py`: conservative local parser and safe_eval action body generator. `tools/odoo_construction_intake_install.py` and `tools/odoo_construction_mail_action_install.py`: native Execute Code installers, not standalone CLI commands. Three local parser tests passed in `tests/test_odoo_construction_mail.py`.

## A26 separation — 2026-10-21
- Imported A26-118 (task 199), A26-117 (201), A26-002 (202), A26-003 (204). Live verification confirmed exactly one matching A26 attachment per task after correcting the original two-file email grouping.
- Native documents were detached through documents.document for redundant originals; no binary file was deleted. Mismatched attachment links were removed from source task messages. The existing task 199 description was retained.
- Persistent attachment/task automation now separates numeric A26 serials, reuses the canonical task, rejects conflicting same-name checksums, and places the proposal only in description. Four local tests passed; the live correction verified the four current tasks. A new incoming multi-attachment message has not yet been tested end-to-end.
- Proof: outputs/a26_one_file_per_transaction_20261021.png.
