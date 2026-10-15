# `signal` binary frame (schema_version 1.3)

The fast channel carries waveforms and spectrogram columns as **binary WebSocket messages**, downsampled on the server. Each message is one frame for one channel:

```
offset  size        field
0       4           header_len   uint32, little-endian: byte length of the JSON header
4       header_len  header       UTF-8 JSON (validated by schemas/signal_header.schema.json)
4+H     4*N         payload      float32 little-endian, N = header.n_samples
```

The total frame length is `4 + header_len + 4 * n_samples` bytes. If needed, the server pads the header with trailing spaces so the payload starts on a 4-byte boundary, which lets the UI read it with `new Float32Array(buf, 4 + header_len, n_samples)` without copying.

## Header fields

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | `"1.3"` |
| `sent_at` | number | unix seconds; also feeds the UI's 3 s "diagnostics offline" watchdog |
| `channel` | string | `mic`, `accel`, `crank_speed`, `mic_stft`, `accel_stft`, … |
| `kind` | `waveform` \| `spectrogram` | how to read the payload |
| `t0` | number | stream time (s) of the first sample or column, on the same clock as `state.t` |
| `sample_rate_hz` | number | rate **after** server downsampling (the column rate for spectrograms) |
| `n_samples` | integer | number of float32 values in the payload |
| `dtype` | `"float32"` | always float32 |
| `n_bins`, `n_frames`, `f_max_hz` | integer, integer, number | **spectrogram only**: `n_samples = n_frames * n_bins`, row-major `[frame][bin]`, with bins linear from 0 to `f_max_hz` |

Example header: [`examples/signal_header.json`](examples/signal_header.json).

## Reading a frame (sketch)

```js
const dv = new DataView(buf);
const hlen = dv.getUint32(0, true);
const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hlen)));
const data = new Float32Array(buf, 4 + hlen, header.n_samples);
```

```python
import json, struct, numpy as np
hlen = struct.unpack_from("<I", buf, 0)[0]
header = json.loads(buf[4:4 + hlen])
data = np.frombuffer(buf, dtype="<f4", count=header["n_samples"], offset=4 + hlen)
```
