import { VOICE_BITRATES, type CodecState, type ImageCodec, type VoiceCodec } from '../api/types';
import { useMesh } from '../state/MeshProvider';

const IMAGE_OPTIONS: { id: ImageCodec; label: string }[] = [
  { id: 'neural', label: 'Neural' },
  { id: 'jpeg', label: 'JPEG' },
];
const VOICE_OPTIONS: { id: VoiceCodec; label: string }[] = [
  { id: 'neural', label: 'EnCodec' },
  { id: 'raw', label: 'Original' },
];

const down = (s: CodecState | undefined) => s === 'unavailable' || s === 'off';

/** How pictures and voice are compressed before they cross the mesh. */
export function CodecPicker() {
  const { codec, setCodec, voiceCodec, voiceBitrate, setVoice, mesh } = useMesh();
  const neuralState = mesh?.codecs.neural;
  const voiceState = mesh?.codecs.voice;

  return (
    <div className="codec" role="group" aria-label="Compression">
      <div className="codec__row">
        <span className="codec__label">Image</span>
        <div className="segmented codec__choices">
          {IMAGE_OPTIONS.map((o) => (
            <button
              key={o.id}
              type="button"
              className="segmented__btn"
              aria-pressed={codec === o.id}
              disabled={o.id === 'neural' && down(neuralState)}
              onClick={() => setCodec(o.id)}
            >
              {o.label}
              {o.id === 'neural' && neuralState === 'loading' ? ' …' : ''}
            </button>
          ))}
        </div>
      </div>
      <div className="codec__row">
        <span className="codec__label">Voice</span>
        <div className="segmented codec__choices">
          {VOICE_OPTIONS.map((o) => (
            <button
              key={o.id}
              type="button"
              className="segmented__btn"
              aria-pressed={voiceCodec === o.id}
              disabled={o.id === 'neural' && down(voiceState)}
              onClick={() => setVoice(o.id, voiceBitrate)}
            >
              {o.label}
              {o.id === 'neural' && voiceState === 'loading' ? ' …' : ''}
            </button>
          ))}
        </div>
      </div>
      {voiceCodec === 'neural' && (
        <div className="codec__row">
          <label className="codec__label" htmlFor="voice-bitrate">Voice bitrate</label>
          <select
            id="voice-bitrate"
            className="codec__select"
            value={voiceBitrate}
            onChange={(e) => setVoice('neural', Number(e.target.value))}
          >
            {VOICE_BITRATES.map((b) => (
              <option key={b} value={b}>{b} kbps</option>
            ))}
          </select>
        </div>
      )}
    </div>
  );
}
