import type { ReactNode } from 'react';

interface DonutProps {
  size: number;
  stroke: number;
  /** 0–100 */
  percent: number;
  color?: string;
  track?: string;
  label: string;
  className?: string;
  children?: ReactNode;
}

export function Donut({
  size,
  stroke,
  percent,
  color = 'var(--color-primary)',
  track = 'var(--color-aqua-tint)',
  label,
  className,
  children,
}: DonutProps) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const p = Math.max(0, Math.min(100, percent));
  const mid = size / 2;
  return (
    <div
      className={`donut${className ? ` ${className}` : ''}`}
      style={{ width: size, height: size }}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(p)}
      aria-label={label}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle cx={mid} cy={mid} r={r} fill="none" stroke={track} strokeWidth={stroke} />
        {p > 0 && (
          <circle
            className="donut__arc"
            cx={mid}
            cy={mid}
            r={r}
            fill="none"
            stroke={color}
            strokeWidth={stroke}
            strokeLinecap={p >= 100 ? 'butt' : 'round'}
            strokeDasharray={`${(p / 100) * c} ${c}`}
            transform={`rotate(-90 ${mid} ${mid})`}
          />
        )}
      </svg>
      <div className="donut__center">{children}</div>
    </div>
  );
}
