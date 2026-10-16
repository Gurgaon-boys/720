"""Real-audio pipeline for AudioSet clips (plan sections 4, 13; DECISIONS D12).

Real clips have no crank reference and an unknown cylinder count and RPM, so this pipeline uses
only features that don't depend on them, never builds a 720 deg fingerprint and never localises.
"""
