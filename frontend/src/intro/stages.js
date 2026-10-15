// The scroll story, keyed to the fly-through video (public/intro-frames/, 120 frames, 10 s).
// `from`/`to` are seconds in the video. The last stage plays after the video ends.

export const VIDEO = {
  frameCount: 120,
  duration: 10,
  src: (i) => `${import.meta.env.BASE_URL}intro-frames/f${String(i + 1).padStart(3, '0')}.webp`,
};

export const STAGES = [
  {
    id: 'vehicle', rail: 'Vehicle', from: 0, to: 1.3,
    caption: () => ({
      title: 'Every engine has a voice.',
      body: 'A microphone and one accelerometer listen to a running engine. Scroll to follow the sound inside.',
    }),
  },
  {
    id: 'xray', rail: 'X-ray', from: 1.3, to: 2.3,
    caption: () => ({
      title: 'Look through the bodywork.',
      body: 'Sensors sit on the block and near the intake. OBD-II adds RPM, load, coolant and fuel trim at its own ~1 Hz rate.',
    }),
  },
  {
    id: 'engine', rail: 'Engine', from: 2.3, to: 4.6,
    caption: ({ cylinder, label }) => ({
      title: 'Four cylinders, one rhythm.',
      body: cylinder && label !== 'healthy'
        ? `Firing order 1-3-4-2. Each combustion leaves a mark in the signal. Cylinder ${cylinder} is breaking the pattern.`
        : 'Firing order 1-3-4-2. Each combustion leaves a mark in the signal.',
    }),
  },
  {
    id: 'cylinder', rail: 'Spark', from: 4.6, to: 6.0,
    caption: () => ({
      title: 'Inside the cylinder.',
      body: 'The spark fires near top dead centre. What happens next decides whether the engine runs clean.',
    }),
  },
  {
    id: 'combustion', rail: 'Combustion', from: 6.0, to: 10,
    caption: ({ abnormal, hint }) => ({
      title: abnormal ? 'Hear the fault.' : 'A clean burn.',
      body: hint
        ? `${hint}. FFT and STFT pull it out of the noise.`
        : 'FFT and STFT turn the pressure wave into frequencies we can measure.',
    }),
  },
  {
    id: 'signature', rail: 'Signature', from: null, to: null,
    caption: ({ geometry }) => (geometry
      ? {
        title: 'One cycle, one fingerprint.',
        body: 'Each petal is a cylinder firing. A healthy engine draws a clean flower; faults bend its shape.',
      }
      : {
        title: 'No fingerprint on real clips.',
        body: 'Engine geometry is unknown on real recordings, so there is no crank-angle view. The spectral view carries the evidence.',
      }),
  },
];
