// node --test: plain-language explanation (D139).
import test from 'node:test';
import assert from 'node:assert/strict';
import { explain } from './explain.js';

const sensors = (over = {}) => ({
  mic: { commanded: false, health: 'NORMAL' },
  accel: { commanded: false, health: 'NORMAL' },
  crank: { commanded: false, health: 'NORMAL' },
  cam: { commanded: false, health: 'NORMAL' },
  ...over,
});
const alarm = {
  decision: {
    state: 'ALARM', alarm_id: 'a_0009', acked: false, fault: 'knock', location: 3,
    witnesses: ['physics', 'classifier'], guidance: 'Reduce load; check fuel octane and spark timing',
  },
};

test('MIC + ACCEL cut during a knock alarm: what happened, what it means, what to do', () => {
  const e = explain({
    state: alarm,
    sensors: sensors({ mic: { commanded: true, health: 'FAILURE' }, accel: { commanded: true, health: 'FAILURE' } }),
    level: 'PARTIAL',
  });
  assert.equal(e.headline, 'ACCEL and MIC are offline. The alarm stays on, but 720 can no longer confirm new faults.');
  assert.match(e.happened[0].text, /^The vibration sensor \(accelerometer\) stopped sending data \(confirmed/);
  assert.ok(e.means.some((m) => /Knock can no longer be detected/.test(m.text)));
  assert.ok(e.means.some((m) => /Only the sound picture/.test(m.text)));
  assert.ok(e.means.some((m) => /\(a_0009\) stays on/.test(m.text)));
  assert.ok(e.means.some((m) => /WATCH at most, never ALARM/.test(m.text)));
  assert.match(e.todo[0].text, /accelerometer.*then press Enable ACCEL\.$/);
  assert.equal(e.todo[0].urgent, true);
  assert.ok(e.todo.some((t) => t.text === 'Acknowledge alarm a_0009 so the record shows an operator saw it.'));
  assert.ok(e.todo.some((t) => t.text === 'For the engine itself: Reduce load; check fuel octane and spark timing'));
});

test('a command not yet confirmed says it is waiting for the watchdog', () => {
  const e = explain({ state: alarm, sensors: sensors({ accel: { commanded: true, health: 'NORMAL' } }), level: 'PARTIAL' });
  assert.match(e.happened[0].text, /waiting for the 3-second confirmation/);
});

test('CAM only: diagnosis unaffected', () => {
  const e = explain({ state: { decision: { state: 'NORMAL', witnesses: [] } }, sensors: sensors({ cam: { commanded: true, health: 'FAILURE' } }), level: 'FULL' });
  assert.equal(e.headline, 'CAM is offline. Diagnosis is unaffected.');
});

test('confirmed alarm without sabotage explains the fault in plain words and quotes the backend guidance', () => {
  const e = explain({ state: alarm, sensors: sensors(), level: 'FULL' });
  assert.equal(e.headline, 'ALARM: engine knock (fuel igniting too early) on cylinder 3');
  assert.match(e.means[0].text, /the physics check and the AI check/);
  assert.ok(e.todo.some((t) => t.text === 'Reduce load; check fuel octane and spark timing'));
});

test('healthy: names no fault', () => {
  const e = explain({ state: { decision: { state: 'NORMAL', witnesses: [] } }, sensors: sensors(), level: 'FULL' });
  assert.equal(e.headline, 'All sensors live: full evidence');
  assert.ok(![...e.happened, ...e.means].some((x) => /misfire|knock|clog/i.test(x.text)));
});
