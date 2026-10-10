import { useRef, useState, type FormEvent } from 'react';
import { errorMessage, sendTextMessage } from '../api/client';
import { useMesh } from '../state/MeshProvider';
import { Icon } from './Icon';

const MAX_LENGTH = 1000;

export function MessageBar() {
  const { upsert, me, peer } = useMesh();
  const inputRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasText = text.trim().length > 0;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!hasText || busy) return;
    setBusy(true);
    setError(null);
    try {
      upsert(await sendTextMessage(text.trim(), { fromNode: me, toNode: peer }));
      setText('');
    } catch (err) {
      setError(errorMessage(err, 'Couldn’t send the message. Try again.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="message-bar" onSubmit={onSubmit}>
      {/* The whole pill focuses the input, so any tap starts typing. */}
      <div className="message-bar__field" onClick={() => inputRef.current?.focus()}>
        <Icon name="message" size={20} strokeWidth={1.8} className="message-bar__icon" />
        <input
          ref={inputRef}
          type="text"
          aria-label="Message"
          placeholder="Type a message…"
          value={text}
          maxLength={MAX_LENGTH}
          enterKeyHint="send"
          autoComplete="off"
          onChange={(e) => setText(e.target.value)}
        />
        {hasText ? (
          <button type="submit" className="message-bar__send" aria-label="Send message" disabled={busy}>
            <Icon name="send" size={20} strokeWidth={2.2} />
          </button>
        ) : (
          <span className="message-bar__hint" aria-hidden="true">
            <Icon name="pencil" size={16} />
            Tap to text
          </span>
        )}
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
    </form>
  );
}
