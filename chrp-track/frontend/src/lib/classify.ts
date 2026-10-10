// Auto-classify a picked file. Keep in sync with backend/app/classify.py.
import type { FileType } from '../api/types';

const EXTENSIONS: Record<FileType, string[]> = {
  text: ['.txt', '.md', '.csv', '.json', '.log'],
  audio: ['.aac', '.opus', '.ogg', '.oga', '.wav', '.m4a', '.mp3', '.amr', '.caf', '.flac'],
  video: ['.mp4', '.m4v', '.mov', '.h265', '.hevc', '.h264', '.webm', '.3gp', '.mkv'],
  image: ['.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.gif', '.bmp'],
};

export const ACCEPT = [
  ...Object.values(EXTENSIONS).flat(),
  'text/plain',
  'audio/*',
  'video/*',
  'image/*',
].join(',');

export function classifyFile(name: string, mime: string): FileType | null {
  const dot = name.lastIndexOf('.');
  const ext = dot >= 0 ? name.slice(dot).toLowerCase() : '';
  for (const [type, exts] of Object.entries(EXTENSIONS) as [FileType, string[]][]) {
    if (exts.includes(ext)) return type;
  }
  const prefix = mime.split('/')[0];
  if (prefix === 'text' || prefix === 'audio' || prefix === 'video' || prefix === 'image') return prefix;
  return null;
}
