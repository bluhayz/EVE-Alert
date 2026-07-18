"""Vision pipeline benchmark harness (#175, v7.2).

Dev-only tool -- lives outside evealert/, so hatchling's build (which only
includes /evealert, see pyproject.toml [tool.hatch.build]) never ships it.

Feeds a directory of captured frames through Vision.find() in a loop and
reports ms/frame, so a performance change can be measured before/after
rather than guessed at. Repeating the same frames across --iterations
models the common "Local chat hasn't changed" steady-state workload the
frame-change short-circuit targets.

Usage:
    python tools/bench_vision.py
    python tools/bench_vision.py --frames path/to/dir --iterations 200
"""

import argparse
import glob
import os
import sys
import time

import cv2 as cv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evealert.tools.vision import Vision  # noqa: E402


def _default_fixtures_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "bench_fixtures")


def load_frames(frames_dir: str) -> list:
    """Load every frame_*.png in *frames_dir*, sorted for reproducibility."""
    paths = sorted(glob.glob(os.path.join(frames_dir, "frame_*.png")))
    frames = []
    for path in paths:
        img = cv.imread(path)
        if img is not None:
            frames.append((path, img))
    return frames


def run_benchmark(
    frames_dir: str,
    needle_path: str,
    iterations: int = 50,
    threshold: int = 70,
    repeat: int = 20,
) -> dict:
    """Run Vision.find() over every frame, *iterations* times, and time it.

    Each distinct frame is captured *repeat* times in a row before moving
    to the next one -- this models the real steady-state workload (Local
    chat stays visually unchanged for many consecutive ~100ms polls, then
    changes) rather than an adversarial cycle-through-N-distinct-frames
    pattern that would defeat the last-frame cache on every single call.

    Returns a dict of results rather than printing directly, so this is
    also usable as a fast smoke check from tests (see
    tests/test_bench_vision.py) without shelling out.
    """
    vision = Vision([needle_path])
    frames = load_frames(frames_dir)
    if not frames:
        raise RuntimeError(f"No frame_*.png files found in {frames_dir}")

    total_matches = 0
    start = time.perf_counter()
    for _ in range(iterations):
        for _, frame in frames:
            for _ in range(repeat):
                points = vision.find(frame, threshold=threshold)
                total_matches += len(points)
    elapsed = time.perf_counter() - start

    total_frames = iterations * len(frames) * repeat
    return {
        "frame_count": len(frames),
        "iterations": iterations,
        "repeat": repeat,
        "total_frames": total_frames,
        "elapsed_seconds": elapsed,
        "ms_per_frame": (elapsed / total_frames) * 1000 if total_frames else 0.0,
        "total_matches": total_matches,
        "needle_hit_counts": vision.get_needle_hit_counts(),
    }


def run_multi_client_benchmark(
    frames_dir: str,
    needle_path: str,
    n_clients: int,
    iterations: int = 50,
    threshold: int = 70,
    repeat: int = 20,
) -> dict:
    """#174: N independent Vision instances (one per simulated client),
    each with its own frame-cache, processing the same fixture set. cv.
    matchTemplate is CPU-bound, so N asyncio tasks on the single-threaded
    alert loop don't run truly in parallel -- this measures the realistic
    total CPU cost per poll cycle: N clients' worth of matching work,
    back to back, with no cache-sharing benefit across clients (each has
    independent frame content in practice).
    """
    total_elapsed = 0.0
    total_frames = 0
    total_matches = 0
    for _ in range(n_clients):
        result = run_benchmark(frames_dir, needle_path, iterations, threshold, repeat)
        total_elapsed += result["elapsed_seconds"]
        total_frames += result["total_frames"]
        total_matches += result["total_matches"]
    return {
        "n_clients": n_clients,
        "elapsed_seconds": total_elapsed,
        "total_frames": total_frames,
        "ms_per_frame": (total_elapsed / total_frames) * 1000 if total_frames else 0.0,
        "total_matches": total_matches,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", default=_default_fixtures_dir(),
                         help="Directory of frame_*.png files")
    parser.add_argument("--needle", default=None,
                         help="Needle template path (default: <frames>/needle.png)")
    parser.add_argument("--iterations", type=int, default=50,
                         help="Times to replay the full frame set")
    parser.add_argument("--repeat", type=int, default=20,
                         help="Consecutive polls per frame before advancing "
                              "(models Local staying static for N polls)")
    parser.add_argument("--threshold", type=int, default=70)
    parser.add_argument("--clients", type=int, default=1,
                         help="#174: simulate N independent clients each "
                              "scanning their own copy of the frame set")
    args = parser.parse_args()

    needle = args.needle or os.path.join(args.frames, "needle.png")

    if args.clients > 1:
        result = run_multi_client_benchmark(
            args.frames, needle, args.clients, args.iterations, args.threshold, args.repeat
        )
        print(f"Clients: {result['n_clients']}  Total passes: {result['total_frames']}")
        print(f"Total elapsed: {result['elapsed_seconds']:.3f}s")
        print(f"Aggregate ms/frame: {result['ms_per_frame']:.4f}")
        print(f"Total matched points: {result['total_matches']}")
        return

    result = run_benchmark(
        args.frames, needle, args.iterations, args.threshold, args.repeat
    )

    print(f"Frames: {result['frame_count']}  "
          f"Iterations: {result['iterations']}  "
          f"Total passes: {result['total_frames']}")
    print(f"Elapsed: {result['elapsed_seconds']:.3f}s")
    print(f"ms/frame: {result['ms_per_frame']:.4f}")
    print(f"Total matched points: {result['total_matches']}")
    print(f"Needle hit counts: {result['needle_hit_counts']}")


if __name__ == "__main__":
    main()
