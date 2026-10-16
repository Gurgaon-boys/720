// Binary `signal` frames from /ws/signal, laid out as in contract/signal_frame.md:
//   uint32 LE header_len | UTF-8 JSON header (space-padded) | float32 LE payload
// decodeFrame reads one; toUiFrame turns it into the plain objects SignalStrip already draws
// (the same shapes the mock transport emits), so the panels don't care where frames come from.

const SUPPORTED_VERSIONS = ['1.3', '1.4', '1.5']; // same as wsTransport.js; frames didn't change in 1.4
const decoder = new TextDecoder();

export function decodeFrame(buf) {
  const dv = new DataView(buf);
  const hlen = dv.getUint32(0, true);
  const header = JSON.parse(decoder.decode(new Uint8Array(buf, 4, hlen)));
  const n = header.n_samples;
  if (buf.byteLength !== 4 + hlen + 4 * n) {
    throw new Error(`signal frame is ${buf.byteLength} bytes, expected ${4 + hlen + 4 * n}`);
  }
  // the server pads the header so this view needs no copy; fall back to a copy if it didn't
  const data = (4 + hlen) % 4 === 0
    ? new Float32Array(buf, 4 + hlen, n)
    : new Float32Array(buf.slice(4 + hlen, 4 + hlen + 4 * n));
  return { header, data };
}

// waveform → { channel, t0, sent_at, sample_rate_hz, samples }
// spectrogram → { channel: 'stft' (for mic_stft), t0, sent_at, freqs_hz, dt_s, columns }
export function toUiFrame({ header: h, data }) {
  if (!SUPPORTED_VERSIONS.includes(h.schema_version)) {
    throw new Error(`signal frame schema_version ${h.schema_version}, expected one of ${SUPPORTED_VERSIONS.join(', ')}`);
  }
  if (h.dtype !== 'float32') throw new Error(`signal frame dtype ${h.dtype}`);
  const base = { schema_version: h.schema_version, sent_at: h.sent_at, t0: h.t0 };

  if (h.kind === 'waveform') {
    // a plain array: SignalStrip concatenates sample buffers
    return { ...base, channel: h.channel, sample_rate_hz: h.sample_rate_hz, samples: Array.from(data) };
  }
  if (h.kind === 'spectrogram') {
    const { n_bins: bins, n_frames: frames, f_max_hz: fMax } = h;
    if (bins * frames !== h.n_samples) throw new Error(`spectrogram ${frames}×${bins} != ${h.n_samples} values`);
    // bins are linear from 0 to f_max_hz; payload is row-major [frame][bin]
    const freqs_hz = Array.from({ length: bins }, (_, i) => (bins > 1 ? (i * fMax) / (bins - 1) : 0));
    const columns = Array.from({ length: frames }, (_, f) => Array.from(data.subarray(f * bins, (f + 1) * bins)));
    return { ...base, channel: h.channel === 'mic_stft' ? 'stft' : h.channel, freqs_hz, dt_s: 1 / h.sample_rate_hz, columns };
  }
  throw new Error(`unknown signal frame kind ${h.kind}`);
}
