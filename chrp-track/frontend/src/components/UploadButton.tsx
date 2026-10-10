import { useRef, useState, type ChangeEvent } from 'react';
import { errorMessage, uploadFile } from '../api/client';
import { ACCEPT, classifyFile } from '../lib/classify';
import { useMesh } from '../state/MeshProvider';
import { Icon } from './Icon';

export function UploadButton() {
  const { upsert, me, peer, codec, voiceCodec, voiceBitrate } = useMesh();
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onPick(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ''; // allow picking the same file again
    if (!file) return;
    setError(null);
    if (!classifyFile(file.name, file.type)) {
      setError('That file type isn’t supported. Send text, audio, video or an image.');
      return;
    }
    setBusy(true);
    try {
      upsert(await uploadFile(file, { fromNode: me, toNode: peer, imageCodec: codec, voiceCodec, voiceBitrate }));
    } catch (err) {
      setError(errorMessage(err, 'Couldn’t send the file. Try again.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="upload">
      <button
        type="button"
        className={`upload__button${busy ? ' is-busy' : ''}`}
        onClick={() => inputRef.current?.click()}
        disabled={busy}
      >
        <span className="upload__dial" aria-hidden="true">
          <svg className="upload__rings" width="200" height="200" viewBox="0 0 200 200">
            <circle cx="100" cy="100" r="97" fill="none" stroke="var(--color-teal)" strokeWidth="1.5" strokeDasharray="2 7" />
            <circle cx="100" cy="100" r="88" fill="none" stroke="var(--color-aqua)" strokeWidth="1" />
            <circle cx="100" cy="100" r="97" fill="none" stroke="var(--color-primary)" strokeWidth="3" strokeLinecap="round" strokeDasharray="46 700" transform="rotate(-128 100 100)" />
            <circle cx="100" cy="100" r="97" fill="none" stroke="var(--color-violet)" strokeWidth="3" strokeLinecap="round" strokeDasharray="18 700" transform="rotate(40 100 100)" />
          </svg>
          <span className="upload__outer">
            <span className="upload__core">
              <Icon name="upload" size={40} strokeWidth={1.8} />
            </span>
          </span>
        </span>
        <span className="upload__label">{busy ? 'Preparing file…' : 'Tap to send a file'}</span>
      </button>
      <input ref={inputRef} type="file" accept={ACCEPT} hidden onChange={onPick} />
      {error && <p className="form-error" role="alert">{error}</p>}
    </div>
  );
}
