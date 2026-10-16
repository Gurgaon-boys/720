// node --test: Sabotage Lab evidence-availability logic (no browser, no dependencies).
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  classifierWitness, evidenceLevel, integrityNotes, offlineSet, physicsWitness, sensorStatus,
} from './evidence.js';

const rules = (over = {}) => [
  { rule: 'misfire', fired: false, location: null, rule_basis: 'crank_angle', ...over.misfire },
  { rule: 'knock', fired: false, location: null, rule_basis: 'crank_angle', ...over.knock },
  { rule: 'injector_clog', fired: false, location: null, rule_basis: 'obd', ...over.clog },
];
const state = (over = {}) => ({
  models: [{ model: 'SVM', label: 'healthy', p: 0.97 }],
  physics_rules: rules(),
  decision: { state: 'NORMAL', witnesses: [] },
  features_summary: {},
  ...over,
});
const sensors = (over = {}) => ({
  mic: { commanded: false, health: 'NORMAL' },
  accel: { commanded: false, health: 'NORMAL' },
  crank: { commanded: false, health: 'NORMAL' },
  cam: { commanded: false, health: 'NORMAL' },
  ...over,
});

test('sensor status follows the backend: pending until the watchdog confirms', () => {
  assert.equal(sensorStatus({ commanded: false, health: 'NORMAL' }), 'LIVE');
  assert.equal(sensorStatus({ commanded: true, health: 'NORMAL', inflight: 'off' }), 'SENDING');
  assert.equal(sensorStatus({ commanded: true, health: 'NORMAL' }), 'DISABLING');
  assert.equal(sensorStatus({ commanded: true, health: 'FAILURE' }), 'OFFLINE');
  // re-enabled but no data yet: recovering, never "live" before the watchdog says NORMAL
  assert.equal(sensorStatus({ commanded: false, health: 'FAILURE', restoring: true }), 'RECOVERING');
  assert.equal(sensorStatus({ commanded: false, health: 'FAILURE' }), 'FAILURE');
  assert.equal(sensorStatus({ commanded: false, health: undefined }), 'UNKNOWN');
});

test('healthy, all sensors live: full evidence, classifier shown as sent', () => {
  const off = offlineSet(sensors());
  const c = classifierWitness(state(), off);
  assert.deepEqual([c.status, c.label, c.p], ['ACTIVE', 'healthy', 0.97]);
  assert.equal(evidenceLevel(c, off), 'FULL');
  assert.deepEqual(integrityNotes(state(), 'FULL', c), []);
});

test('accelerometer off: classifier unavailable with the exact message, partial evidence', () => {
  const off = offlineSet(sensors({ accel: { commanded: true, health: 'NORMAL' } }));
  const c = classifierWitness(state({ models: [] }), off);
  assert.equal(c.status, 'UNAVAILABLE');
  assert.equal(c.reason, 'Classifier witness unavailable: accelerometer offline. New confirmed alarms require two matching witnesses.');
  assert.equal(evidenceLevel(c, off), 'PARTIAL');
});

test('never invents a classifier output: models from the backend win even if a sensor is off', () => {
  // a window computed just before the cut still carries the model's output: show it, as sent
  const off = offlineSet(sensors({ accel: { commanded: true, health: 'NORMAL' } }));
  const c = classifierWitness(state({ models: [{ model: 'SVM', label: 'misfire', p: 0.95 }] }), off);
  assert.equal(c.status, 'ACTIVE');
  assert.equal(c.label, 'misfire');
  assert.equal(evidenceLevel(c, off), 'PARTIAL'); // but a confirmation is no longer possible
});

test('latched alarm during sensor failure is reported as latched, never downgraded', () => {
  const s = state({ models: [], decision: { state: 'ALARM', latched: true, acked: false, fault: 'misfire', witnesses: ['physics', 'classifier'] } });
  const off = offlineSet(sensors({ accel: { commanded: true, health: 'FAILURE' } }));
  const c = classifierWitness(s, off);
  const level = evidenceLevel(c, off);
  const notes = integrityNotes(s, level, c).map((n) => n.text);
  assert.ok(notes.includes('Previously confirmed alarm remains latched. Current diagnostic evidence is degraded.'));
  assert.equal(s.decision.state, 'ALARM'); // untouched
});

test('crank and accelerometer both off: insufficient evidence; physics misfire rule unavailable', () => {
  const off = offlineSet(sensors({ accel: { commanded: true, health: 'FAILURE' }, crank: { commanded: true, health: 'FAILURE' } }));
  const c = classifierWitness(state({ models: [] }), off);
  assert.equal(evidenceLevel(c, off), 'INSUFFICIENT');
  assert.match(c.reason, /accelerometer and crank sensor|crank sensor and accelerometer/);
  const p = physicsWitness(state({ physics_rules: rules({ misfire: { fired: true, location: 3 } }) }), off);
  const mis = p.find((r) => r.rule === 'misfire');
  assert.equal(mis.available, false);
  assert.equal(mis.fired, false);
  assert.equal(p.find((r) => r.rule === 'injector_clog').available, true); // OBD is not switchable
});

test('mic off: still full evidence (mic is not a diagnostic input)', () => {
  const off = offlineSet(sensors({ mic: { commanded: true, health: 'FAILURE' } }));
  const c = classifierWitness(state(), off);
  assert.equal(evidenceLevel(c, off), 'FULL');
});

test('recovery: classifier evidence returns only when the backend sends model output again', () => {
  const off = offlineSet(sensors()); // sensor back to NORMAL
  assert.equal(classifierWitness(state({ models: [] }), off, true).status, 'NO_OUTPUT');
  assert.equal(classifierWitness(state(), off, true).status, 'ACTIVE');
  assert.equal(classifierWitness(state({ models: [] }), off, false).status, 'NOT_LOADED');
});

test('the SVM row is the classifier witness when several models are sent', () => {
  const c = classifierWitness(state({ models: [
    { model: 'Baseline', label: 'healthy', p: 0.6 }, { model: 'SVM', label: 'knock', p: 0.81 }, { model: 'CNN', label: 'misfire', p: 0.88 },
  ] }), offlineSet(sensors()));
  assert.deepEqual([c.model, c.label, c.p], ['SVM', 'knock', 0.81]);
});
