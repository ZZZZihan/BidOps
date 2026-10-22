CREATE TABLE nd_attachment_receipts (
    record_id TEXT PRIMARY KEY REFERENCES nd_attachments(record_id),
    receipt_sha256 TEXT NOT NULL,
    receipt_json TEXT NOT NULL
);
CREATE TRIGGER nd_attachment_receipts_no_update BEFORE UPDATE ON nd_attachment_receipts
BEGIN SELECT RAISE(ABORT,'nd_immutable_attachment_receipt'); END;
CREATE TRIGGER nd_attachment_receipts_no_delete BEFORE DELETE ON nd_attachment_receipts
BEGIN SELECT RAISE(ABORT,'nd_immutable_attachment_receipt'); END;
