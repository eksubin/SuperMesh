import { useMesh } from '../state/MeshProvider';
import { Icon } from './Icon';

/** Choose which simulated node this phone acts as and which node it sends to. Switch sides to read what arrived. */
export function NodePicker() {
  const { me, peer, setNodes, mesh } = useMesh();
  const count = mesh?.nodes ?? 6;
  const options = Array.from({ length: count }, (_, i) => i);

  function pickMe(next: number) {
    // Never allow talking to yourself: nudge the peer to the next free node.
    setNodes(next, next === peer ? options.find((n) => n !== next) ?? peer : peer);
  }
  function pickPeer(next: number) {
    setNodes(next === me ? options.find((n) => n !== next) ?? me : me, next);
  }

  return (
    <div className="node-picker" role="group" aria-label="Mesh nodes">
      <label className="node-picker__side">
        <span>You are</span>
        <select value={me} onChange={(e) => pickMe(Number(e.target.value))} aria-label="This phone is node">
          {options.map((n) => <option key={n} value={n}>Node {n}</option>)}
        </select>
      </label>
      <button type="button" className="node-picker__swap" onClick={() => setNodes(peer, me)} aria-label="Swap nodes">
        <Icon name="swap" size={18} strokeWidth={2.2} />
      </button>
      <label className="node-picker__side">
        <span>Sending to</span>
        <select value={peer} onChange={(e) => pickPeer(Number(e.target.value))} aria-label="Send to node">
          {options.map((n) => <option key={n} value={n}>Node {n}</option>)}
        </select>
      </label>
    </div>
  );
}
