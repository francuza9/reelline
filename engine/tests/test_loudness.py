import numpy as np
from reelline.media.loudness import rms_per_frame


def test_one_value_per_video_frame_normalized_to_one():
    rate, fps = 16000, 30
    t = np.arange(int(rate * 1.01)) / rate
    samples = np.sin(2 * np.pi * 220 * t) * np.where(t < 0.5, 0.2, 0.8)
    loud = rms_per_frame(samples, rate, fps)
    assert len(loud) == 31  # ceil(1.01 s * 30)
    assert loud.max() == 1.0
    assert loud[5] < 0.3 and loud[20] > 0.9


def test_silence_is_all_zero():
    assert not rms_per_frame(np.zeros(16000), 16000, 30).any()
