import { sourceInfo } from '../lib/faults.js';

// Two sound toggles, both off by default (browsers also require a click before any audio).
// "Listen" plays the stream's mic_audio channel; without one, the 4 kHz mic waveform the plots
// use (audioKind 'display'), which carries the firing rhythm but nothing above 2 kHz, so no knock
// ringing. It is labelled with the data source and, for the fallback, with what it is.
export default function SoundControls({ sound, onToggle, audioKind, source }) {
  const src = sourceInfo(source).name;
  const available = audioKind != null;
  // "on" only when audio is actually playing, never just because the toggle was left on
  const playing = sound.engine && available;
  const why = source === 'real'
    ? 'No audio for real clips yet: needs the AudioSet recording'
    : 'This stream has no audio channel';

  return (
    <div className="sound" role="group" aria-label="Sound">
      <button
        type="button"
        className="sound-btn"
        aria-pressed={playing}
        disabled={!available}
        title={!available ? why : audioKind === 'display' ? 'Plays the 4 kHz mic stream: firing rhythm and misfires are audible, knock (6–7 kHz) is not' : undefined}
        onClick={() => onToggle('engine')}
      >
        <span aria-hidden="true">{playing ? '■ ' : '▶ '}</span>
        {playing ? 'Stop listening' : 'Listen'}
        {available && <span className="mono">{src}</span>}
      </button>
      <button type="button" className="sound-btn" aria-pressed={sound.alarm} onClick={() => onToggle('alarm')}>
        Alarm tone {sound.alarm ? 'on' : 'off'}
      </button>
      {!available && <span className="sound-why">{why}</span>}
      {audioKind === 'display' && <span className="sound-why">4 kHz mic stream: no knock band</span>}
    </div>
  );
}
