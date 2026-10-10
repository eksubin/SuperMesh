import { useEffect, useRef, useState } from 'react';
import { errorMessage, uploadFile } from '../api/client';
import { blobToWav24k } from '../lib/wav';
import { useMesh } from '../state/MeshProvider';
import { Icon } from './Icon';

// Without the neural codec the recording itself crosses the mesh: Opus at 6 kbit/s is about 750 bytes per second.
const RAW_BITS_PER_SECOND = 6000;
// With it, record cleanly (the codec does the compressing) and hand the server a 24 kHz WAV.
const CLEAN_BITS_PER_SECOND = 32000;
const MAX_SECONDS = 8;

type Phase = 'idle' | 'recording' | 'sending';

export function VoiceRecorder() {
  const { upsert, me, peer, voiceCodec, voiceBitrate } = useMesh();
  const neural = voiceCodec === 'neural';
  const [phase, setPhase] = useState<Phase>('idle');
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const discard = useRef(false);

  useEffect(() => () => stopTracks(), []);

  function stopTracks() {
    window.clearInterval(timer.current);
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
  }

  async function start() {
    setError(null);
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setError('Recording needs a secure page (https or localhost) and a browser with microphone support.');
      return;
    }
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError('Microphone access was blocked. Allow it in the browser settings.');
      return;
    }
    const opts: MediaRecorderOptions = { audioBitsPerSecond: neural ? CLEAN_BITS_PER_SECOND : RAW_BITS_PER_SECOND };
    if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) opts.mimeType = 'audio/webm;codecs=opus';
    const rec = new MediaRecorder(stream.current, opts);
    const parts: Blob[] = [];
    discard.current = false;
    rec.ondataavailable = (e) => e.data.size && parts.push(e.data);
    rec.onstop = async () => {
      stopTracks();
      if (discard.current || !parts.length) {
        setPhase('idle');
        return;
      }
      const mime = rec.mimeType || 'audio/webm';
      const ext = mime.includes('mp4') ? 'm4a' : mime.includes('ogg') ? 'ogg' : 'webm';
      let file = new File(parts, `voice-message.${ext}`, { type: mime });
      setPhase('sending');
      try {
        if (neural) {
          try {
            file = new File([await blobToWav24k(file)], 'voice-message.wav', { type: 'audio/wav' });
          } catch {
            /* this browser can't convert it: the server will try the original */
          }
        }
        upsert(await uploadFile(file, { fromNode: me, toNode: peer, fileType: 'audio', voiceCodec, voiceBitrate }));
      } catch (err) {
        setError(errorMessage(err, 'Couldn’t send the voice message. Try again.'));
      } finally {
        setPhase('idle');
      }
    };
    recorder.current = rec;
    rec.start();
    setSeconds(0);
    setPhase('recording');
    timer.current = window.setInterval(() => {
      setSeconds((s) => {
        if (s + 1 >= MAX_SECONDS) rec.state === 'recording' && rec.stop();
        return s + 1;
      });
    }, 1000);
  }

  function stopAndSend() {
    if (recorder.current?.state === 'recording') recorder.current.stop();
  }
  function cancel() {
    discard.current = true;
    if (recorder.current?.state === 'recording') recorder.current.stop();
  }

  return (
    <div className="voice">
      {phase === 'recording' ? (
        <div className="voice__bar voice__bar--live" role="status">
          <span className="voice__dot" aria-hidden="true" />
          <span className="voice__time">0:{String(seconds).padStart(2, '0')} / 0:{String(MAX_SECONDS).padStart(2, '0')}</span>
          <button type="button" className="voice__cancel" onClick={cancel}>Cancel</button>
          <button type="button" className="message-bar__send" onClick={stopAndSend} aria-label="Stop and send voice message">
            <Icon name="send" size={20} strokeWidth={2.2} />
          </button>
        </div>
      ) : (
        <button type="button" className="voice__bar" onClick={start} disabled={phase === 'sending'}>
          <Icon name="audio" size={20} strokeWidth={1.8} className="message-bar__icon" />
          <span>{phase === 'sending' ? 'Preparing voice message…' : 'Tap to record a voice message'}</span>
        </button>
      )}
      {error && <p className="form-error" role="alert">{error}</p>}
    </div>
  );
}
