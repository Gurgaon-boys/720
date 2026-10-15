import { sourceInfo } from '../lib/faults.js';

// Two sound toggles, both off by default (browsers also require a click before any audio).
// "Listen" plays the stream's mic_audio channel; it is labelled with the data source.
export default function SoundControls({ sound, onToggle, audioAvailable, source }) {
  const src = sourceInfo(source).name;
  // "on" only when audio is actually playing, never just because the toggle was left on
  const playing = sound.engine && audioAvailable;
  const why = source === 'real'
    ? 'No audio for real clips yet: needs the AudioSet recording'
    : 'This stream has no audio channel';

  return (
    <div className="sound" role="group" aria-label="Sound">
      <button
        type="button"
        className="sound-btn"
        aria-pressed={playing}
        disabled={!audioAvailable}
        title={audioAvailable ? undefined : why}
        onClick={() => onToggle('engine')}
      >
        <span aria-hidden="true">{playing ? '■ ' : '▶ '}</span>
        {playing ? 'Stop listening' : 'Listen'}
        {audioAvailable && <span className="mono">{src}</span>}
      </button>
      <button type="button" className="sound-btn" aria-pressed={sound.alarm} onClick={() => onToggle('alarm')}>
        Alarm tone {sound.alarm ? 'on' : 'off'}
      </button>
      {!audioAvailable && <span className="sound-why">{why}</span>}
    </div>
  );
}
