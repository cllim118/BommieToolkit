import argparse
import os
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from typing import Optional

import cv2
from tqdm import tqdm

def probe_timecode(path: str) -> Optional[str]:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "d:0",
        "-show_entries", "stream_tags=timecode",
        "-of", "default=nw=1:nk=1",
        path,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        val = out.stdout.strip()
        return val or None
    except subprocess.CalledProcessError as e:
        print("ffprobe error:", e.stderr.strip())
        return None

def probe_fps(path: str) -> Optional[float]:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "default=nokey=1:noprint_wrappers=1",
        path,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        fps_str = out.stdout.strip()
        if fps_str:
            # fps_str is often a fraction like "30000/1001"
            if "/" in fps_str:
                num, denom = map(int, fps_str.split("/"))
                return num / denom
            else:
                return float(fps_str)
        return None
    except subprocess.CalledProcessError as e:
        print("ffprobe error:", e.stderr.strip())
        return None

def timecode_to_ns(tc: str, fps) -> int:
    """
    Convert 'HH:MM:SS:FF' to nanoseconds.
    - fps can be a float (e.g., 25.0, 29.97) or a Fraction (e.g., Fraction(30000,1001)).
    - Non–drop-frame math (i.e., straight frame counting).
    """
    if isinstance(fps, (int, float)):
        # snap common fractional frame rates to exact rationals
        if abs(fps - 29.97) < 1e-6: fps = Fraction(30000, 1001)
        elif abs(fps - 59.94) < 1e-6: fps = Fraction(60000, 1001)
        else: fps = Fraction(str(fps))  # exact rational from decimal string

    tc = tc.replace(";", ":")
    hh, mm, ss, ff = map(int, tc.split(":"))
    total_seconds = Fraction(hh*3600 + mm*60 + ss, 1) + Fraction(ff, 1) / fps
    return int(round(total_seconds * 1_000_000_000))

# Target pixel counts for the --resolution presets (aspect ratio is preserved, never upscaled)
TARGET_PIXELS = {
    'lowest': 320 * 240,
    'low': 640 * 480,
    'medium': 1920 * 1080,
    'high': 3840 * 2160,
}
IMAGE_FORMATS = ['png', 'jpg', 'jpeg', 'bmp', 'tiff']


def main():
    parser = argparse.ArgumentParser(description='Extract timestamp-named frames (from the GoPro timecode) from a video')
    parser.add_argument('--video', type=str, required=True, help='Path to video file')
    parser.add_argument('--output', type=str, required=True, help='Output directory (wiped if it exists)')
    parser.add_argument('--resolution', type=str, choices=list(TARGET_PIXELS), default=None,
                        help='Downscale preset: lowest (~320x240), low (~640x480), medium (~1920x1080), high (~3840x2160)')
    parser.add_argument('--gray', action='store_true', help='Save frames in grayscale')
    parser.add_argument('--format', type=str, choices=IMAGE_FORMATS, default='png', help='Image format')
    parser.add_argument('--max_frames', type=int, default=None, help='Maximum number of frames to extract (0 = no limit)')
    parser.add_argument('--skip', type=float, default=0.0, help='Seconds to skip at start of video')
    parser.add_argument('--sample_step', type=int, default=1, help='Keep every Nth frame (1 = keep all frames)')
    parser.add_argument('--vis', action='store_true', help='Show frames while extracting')
    args = parser.parse_args()

    sample_step = max(1, args.sample_step)

    fps = probe_fps(args.video)
    tc = probe_timecode(args.video)
    if fps is None or tc is None:
        sys.exit(f'Could not read fps/timecode from {args.video} (frames are named by the timecode)')

    folder_output = Path(args.output)
    if folder_output.exists() and folder_output.is_dir():
        shutil.rmtree(folder_output)
    folder_output.mkdir(parents=True, exist_ok=True)

    # Time synchronization via timecode
    time_ns = 1000000000000000000 + timecode_to_ns(tc, fps)
    delta = int(1.0 / fps * 1e9)

    cap = cv2.VideoCapture(args.video)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    frames_to_skip = 0
    if args.skip > 0.0:
        frames_to_skip = int(args.skip / (1.0 / fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frames_to_skip)
        time_ns += frames_to_skip * delta

    scale_factor = None
    counter = 0
    for i in tqdm(range(frame_count - frames_to_skip), desc='Extracting'):
        time_ns += delta

        # Skip frames that are not on the sampling grid (grab() avoids decoding cost)
        if i % sample_step != 0:
            cap.grab()
            continue

        ret, img = cap.read()
        if not ret:
            continue

        if scale_factor is None:
            scale_factor = 1.0
            if args.resolution is not None:
                h0, w0 = img.shape[:2]
                scale_factor = min(1.0, (TARGET_PIXELS[args.resolution] / float(w0 * h0)) ** 0.5)

        width = int(img.shape[1] * scale_factor)
        height = int(img.shape[0] * scale_factor)
        res_img = cv2.resize(img, (width, height), interpolation=cv2.INTER_AREA)
        if args.gray:
            res_img = cv2.cvtColor(res_img, cv2.COLOR_BGR2GRAY)

        if args.vis:
            cv2.imshow('Frame', res_img)
            cv2.waitKey(30)

        cv2.imwrite(os.path.join(args.output, f'{time_ns:019d}.{args.format}'), res_img)
        counter += 1

        if args.max_frames and counter >= args.max_frames:
            break

    cap.release()
    if args.vis:
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()