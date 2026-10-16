import { useState } from 'react';

// EXPORT PDF REPORT (D143): downloads GET /report.pdf (built on the backend from recorded data only).
// If the backend can't make a PDF (reportlab missing → 503) or the request fails, it opens the printable
// HTML report instead, where the browser's "Save as PDF" does the same job.

export default function ExportButton({ api, className = 'lab-btn primary', label = 'Export PDF report' }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  if (!api) return null;
  const go = async () => {
    setBusy(true);
    setNote(null);
    try {
      const res = await fetch(api.pdfUrl, { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const name = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') ?? '')?.[1] ?? '720_incident_report.pdf';
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      setNote(`Saved ${name}`);
    } catch {
      window.open(api.reportUrl, '_blank', 'noopener');
      setNote('PDF unavailable: opened the printable report (use Save as PDF)');
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="export">
      <button type="button" className={className} onClick={go} disabled={busy}>{busy ? 'Preparing PDF…' : label}</button>
      {note && <small className="export-note" role="status">{note}</small>}
    </span>
  );
}
