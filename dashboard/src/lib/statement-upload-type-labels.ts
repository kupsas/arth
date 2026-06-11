/**
 * Labels for **unified statement upload** (`POST /api/pipeline/upload`): bank formats plus
 * portfolio statements routed after transaction detectors miss. Keep in sync with
 * ``pipeline/detection.py`` ``PARSER_LABELS`` keys surfaced to users.
 */
export const TRANSACTION_UPLOAD_TYPE_LABELS: readonly string[] = [
  "HDFC Savings Account Statement (.txt export)",
  "HDFC Combined Bank Statement (PDF)",
  "HDFC Credit Card Statement (.csv export)",
  "HDFC Credit Card Statement (PDF)",
  "ICICI Bank Savings Account Statement (PDF)",
  "ICICI Direct Equity Transaction Statement (PDF)",
  "ICICI Direct Mutual Fund Statement (PDF)",
  "ICICI PPF Account Statement (PDF)",
  "Zerodha Tradebook (CSV export)",
  "Zerodha Monthly Demat Statement (PDF)",
]
