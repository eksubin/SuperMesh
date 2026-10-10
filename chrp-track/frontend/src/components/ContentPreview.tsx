import { useEffect, useState } from 'react';
import { contentUrl } from '../api/client';
import type { MeshTransmissionEvent } from '../api/types';

/** Shows what was actually sent or received: the (shrunken) image, a voice player, or the text. */
export function ContentPreview({ tx }: { tx: MeshTransmissionEvent }) {
  const [text, setText] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const isText = tx.fileType === 'text';

  useEffect(() => {
    setFailed(false);
    if (!tx.hasContent || !isText) return;
    let cancelled = false;
    fetch(contentUrl(tx.fileId))
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((t) => !cancelled && setText(t))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [tx.fileId, tx.hasContent, isText]);

  if (!tx.hasContent) return null;
  const url = contentUrl(tx.fileId);
  const label = tx.direction === 'incoming' ? 'Received content' : 'Sent content';

  return (
    <section className="content-card">
      <h2 className="section__title">{label}</h2>
      {failed ? (
        <p className="empty empty--plain">The content isn’t available any more.</p>
      ) : tx.fileType === 'image' ? (
        <img className="content-card__image" src={url} alt={`${tx.fileName} as it travelled over the mesh`} onError={() => setFailed(true)} />
      ) : tx.fileType === 'audio' ? (
        <audio className="content-card__audio" controls preload="metadata" src={url} onError={() => setFailed(true)} />
      ) : tx.fileType === 'video' ? (
        <video className="content-card__image" controls preload="metadata" src={url} onError={() => setFailed(true)} />
      ) : (
        <p className="content-card__text">{text ?? 'Loading…'}</p>
      )}
      {tx.fileType === 'image' && <small className="content-card__note">Compressed before sending so it fits in a few radio packets.</small>}
    </section>
  );
}
