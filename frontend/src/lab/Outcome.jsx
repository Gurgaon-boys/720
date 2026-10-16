import { explain } from './explain.js';

// WHAT JUST HAPPENED (D141, D142): the result of the judge's action, explained for someone who doesn't know
// the system. One headline, then three columns: what happened, what it means, what to do now. A thin strip
// underneath lists what 720 lost and what still works. Every statement comes from explain.js, which only
// rephrases backend facts and the fixed sensor dependency table.

const KEY_LINE = 'You took away a sensor, so 720 stopped claiming what it could no longer prove.';
const ICON = { happened: '●', means: '◆', todo: '→' };

function Column({ kind, title, items, numbered }) {
  return (
    <div className={`ox-col ox-${kind}`}>
      <h4><span aria-hidden="true">{ICON[kind]}</span>{title}</h4>
      {numbered ? (
        <ol className="ox-list numbered">
          {items.map((t, i) => (
            <li key={t.text} className={`k-${t.kind}${t.urgent ? ' urgent' : ''}`}>
              <span className="ox-n">{i + 1}</span>
              <span>{t.text}{t.urgent && <em className="ox-first">first</em>}</span>
            </li>
          ))}
        </ol>
      ) : (
        <ul className="ox-list">
          {items.map((t) => <li key={t.text} className={`tone-${t.tone}`}>{t.text}</li>)}
        </ul>
      )}
    </div>
  );
}

export default function Outcome({ result, state, sensors, level }) {
  const e = explain({ state, sensors, level });
  return (
    <section className={`ox tone-${e.tone}`} aria-label="What just happened" aria-live="polite">
      <div className="ox-head">
        <span className="ox-kicker">WHAT JUST HAPPENED</span>
        <p className="ox-headline">{e.headline}</p>
      </div>
      <div className="ox-cols">
        <Column kind="happened" title="What happened" items={e.happened} />
        <Column kind="means" title="What it means" items={e.means} />
        <Column kind="todo" title="What to do now" items={e.todo} numbered />
      </div>
      {result.removed.length > 0 && (
        <div className="ox-strip">
          <span className="ox-strip-label">720 lost</span>
          {result.lost.length ? result.lost.map((c) => <span key={c.id} className="ox-chip lost">{c.name}</span>)
            : <span className="ox-chip none">nothing diagnostic</span>}
          <span className="ox-strip-label">still working</span>
          {result.kept.filter((c) => c.id !== 'phase').map((c) => <span key={c.id} className="ox-chip kept">{c.name}</span>)}
        </div>
      )}
      {result.classifierGone && <p className="oc-key">{KEY_LINE}</p>}
    </section>
  );
}
