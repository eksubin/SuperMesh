import { paths } from '../lib/router';
import { Icon, type IconName } from './Icon';

type Tab = 'main' | 'history' | 'log';

const TABS: { id: Tab; label: string; icon: IconName; href: string }[] = [
  { id: 'main', label: 'Main', icon: 'radio', href: paths.main },
  { id: 'history', label: 'History', icon: 'history', href: paths.history },
  { id: 'log', label: 'Log', icon: 'log', href: paths.log },
];

export function TabBar({ active }: { active: Tab }) {
  return (
    <nav className="tabbar" aria-label="Main navigation">
      {TABS.map((tab) => (
        <a
          key={tab.id}
          href={tab.href}
          className={`tabbar__item${active === tab.id ? ' is-active' : ''}`}
          aria-current={active === tab.id ? 'page' : undefined}
        >
          <Icon name={tab.icon} />
          {tab.label}
        </a>
      ))}
    </nav>
  );
}
