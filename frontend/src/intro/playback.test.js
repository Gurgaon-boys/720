// node --test: intro autoplay timeline and the play / skip / Esc rules.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  AUTOPLAY_FPS, VIDEO_SHARE, autoplayProgress, autoplayStart, autoplayTimeline, keyToEvent, playbackStep,
} from './playback.js';

const tl = autoplayTimeline(120);
const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} ≉ ${b}`);

test('timeline: 120 frames at 24 fps, about 9 s in all', () => {
  near(tl.video, (119 / 24) * 1000);
  assert.equal(tl.total, 9000);
  assert.ok(tl.rest > 3500, 'the signature still gets several seconds');
});

test('progress: the video plays at 24 fps, then the signature, then it holds at 1', () => {
  assert.equal(autoplayProgress(-5, tl), 0);
  assert.equal(autoplayProgress(0, tl), 0);
  // one second in is frame 24 of 0..119
  near((autoplayProgress(1000, tl) / VIDEO_SHARE) * 119, AUTOPLAY_FPS);
  near(autoplayProgress(tl.video, tl), VIDEO_SHARE);
  assert.ok(autoplayProgress(tl.video + tl.rest / 2, tl) > VIDEO_SHARE);
  assert.equal(autoplayProgress(tl.total, tl), 1);
  assert.equal(autoplayProgress(tl.total + 1000, tl), 1);
});

test('play resumes from the scroll position, and starts over at the end', () => {
  for (const ms of [0, 800, tl.video, tl.video + 1200, tl.total - 100]) near(autoplayStart(autoplayProgress(ms, tl), tl), ms, 1e-6);
  assert.equal(autoplayStart(1, tl), 0);
});

test('play button / Space toggles playing and idle', () => {
  assert.deepEqual(playbackStep('idle', 'toggle'), { mode: 'playing', skip: false });
  assert.deepEqual(playbackStep('playing', 'toggle'), { mode: 'idle', skip: false });
});

test('scrolling during autoplay takes over; the end stops it', () => {
  assert.deepEqual(playbackStep('playing', 'scroll'), { mode: 'idle', skip: false });
  assert.deepEqual(playbackStep('idle', 'scroll'), { mode: 'idle', skip: false });
  assert.deepEqual(playbackStep('playing', 'end'), { mode: 'idle', skip: false });
});

test('Skip and Esc stop playback and jump to the dashboard', () => {
  for (const ev of ['skip', 'escape']) {
    assert.deepEqual(playbackStep('playing', ev), { mode: 'idle', skip: true });
    assert.deepEqual(playbackStep('idle', ev), { mode: 'idle', skip: true });
  }
  assert.deepEqual(playbackStep('playing', 'nonsense'), { mode: 'playing', skip: false });
});

test('keys: Space toggles and keeps the page still; Esc skips', () => {
  const base = { inView: true, playing: false };
  assert.deepEqual(keyToEvent({ ...base, key: ' ' }), { event: 'toggle', prevent: true });
  assert.deepEqual(keyToEvent({ ...base, key: ' ', playing: true }), { event: 'toggle', prevent: true });
  assert.deepEqual(keyToEvent({ ...base, key: 'Escape' }), { event: 'escape', prevent: false });
  assert.deepEqual(keyToEvent({ ...base, key: 'Escape', playing: true }), { event: 'escape', prevent: false });
});

test('keys: scroll keys take over only while playing', () => {
  for (const key of ['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End']) {
    assert.deepEqual(keyToEvent({ key, inView: true, playing: true }), { event: 'scroll', prevent: false });
    assert.equal(keyToEvent({ key, inView: true, playing: false }), null);
  }
  assert.equal(keyToEvent({ key: 'a', inView: true, playing: true }), null);
});

test('keys: left alone off the intro, in inputs, on focused buttons and with modifiers', () => {
  assert.equal(keyToEvent({ key: ' ', inView: false, playing: false }), null);
  assert.equal(keyToEvent({ key: 'Escape', inView: false, playing: true }), null);
  for (const tag of ['BUTTON', 'INPUT', 'TEXTAREA', 'SELECT', 'A']) assert.equal(keyToEvent({ key: ' ', tag, inView: true }), null);
  assert.equal(keyToEvent({ key: ' ', editable: true, inView: true }), null);
  assert.equal(keyToEvent({ key: ' ', modified: true, inView: true }), null);
  assert.equal(keyToEvent({ key: 'Escape', editable: true, inView: true }), null);
});
