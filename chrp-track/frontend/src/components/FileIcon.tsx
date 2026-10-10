import type { CSSProperties } from 'react';
import { contentUrl } from '../api/client';
import type { FileType, MeshTransmissionEvent } from '../api/types';
import { Icon } from './Icon';

const TONES: Record<FileType, { fill: string; ink: string; ring: string }> = {
  text: { fill: 'var(--color-aqua)', ink: 'var(--color-primary-ink)', ring: 'var(--color-teal)' },
  audio: { fill: 'var(--color-lilac)', ink: 'var(--color-plum)', ring: 'var(--color-violet)' },
  video: { fill: 'var(--color-lavender-light)', ink: 'var(--color-plum)', ring: 'var(--color-violet)' },
  image: { fill: 'var(--color-aqua-tint)', ink: 'var(--color-primary-ink)', ring: 'var(--color-teal)' },
};

interface FileIconProps {
  tx: Pick<MeshTransmissionEvent, 'fileType' | 'kind'> & Partial<Pick<MeshTransmissionEvent, 'fileId' | 'hasContent'>>;
  size?: number;
  /** Outline style marks received (incoming) items. */
  outline?: boolean;
}

export function FileIcon({ tx, size = 40, outline = false }: FileIconProps) {
  const tone = TONES[tx.fileType];
  const style: CSSProperties = {
    width: size,
    height: size,
    color: tone.ink,
    background: outline ? 'transparent' : tone.fill,
    border: outline ? `2px solid ${tone.ring}` : 'none',
  };
  // Images that were sent or received show their own thumbnail.
  if (tx.fileType === 'image' && tx.hasContent && tx.fileId) {
    return (
      <span className="file-icon file-icon--thumb" style={{ ...style, border: `2px solid ${tone.ring}` }}>
        <img src={contentUrl(tx.fileId)} alt="" width={size} height={size} />
      </span>
    );
  }
  return (
    <span className="file-icon" style={style}>
      <Icon name={tx.kind === 'message' ? 'message' : tx.fileType} size={Math.round(size * 0.5)} />
    </span>
  );
}
