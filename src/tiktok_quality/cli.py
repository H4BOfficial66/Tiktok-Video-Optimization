"""CLI interface for tiktok-quality.

Usage:
    # 7-u8 Smoothness / FPS patch (Recommended: preserves exact duration, 0% stretch)
    tiktok-quality patch input.mp4 output.mp4 --fps-in 60 --fps-out 30

    # Inspect a published TikTok URL to verify if 1080p 60fps was retained
    tiktok-quality check https://www.tiktok.com/@user/video/123456789

    # Legacy Ghost frames transform
    tiktok-quality ghost input.mp4 output.mp4 -m 10
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .fps_patch import detect_fps, patch_fps
from .linkcheck import check_tiktok
from .transform import transform


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog='tiktok-quality',
        description='TikTok Video Optimizer (7-u8 FPS Method & Ghost Frame Container Toolkit)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest='command', help='Subcommands')

    # Subcommand: patch (Recommended)
    p_patch = subparsers.add_parser('patch', help='Apply 7-u8 60/120 FPS timescale patch (exact duration, 0% stretch)')
    p_patch.add_argument('input', help='Input MP4 file')
    p_patch.add_argument('output', help='Output MP4 file path')
    p_patch.add_argument('--fps-in', type=float, default=None, help='Source FPS (auto-detected if omitted)')
    p_patch.add_argument('--fps-out', type=float, default=30.0, help='Target container FPS (default: 30)')
    p_patch.add_argument('--mode', choices=('method', 'slowmo'), default='method', help='Patch mode')
    p_patch.add_argument('--no-audio', action='store_true', help='Exclude audio track from scaling')

    # Subcommand: check
    p_check = subparsers.add_parser('check', help='Check a posted TikTok video link (resolutions, fps, bitrates)')
    p_check.add_argument('url', help='TikTok video URL (e.g. https://www.tiktok.com/@user/video/...)')

    # Subcommand: ghost
    p_ghost = subparsers.add_parser('ghost', help='Apply legacy ghost frames NAL transform')
    p_ghost.add_argument('input', help='Input MP4 file (H.264/AVC)')
    p_ghost.add_argument('output', help='Output MP4 file path')
    p_ghost.add_argument('-m', '--multiplier', type=int, default=10, help='Frame multiplier (default: 10)')
    p_ghost.add_argument('-c', '--comment', type=str, default='TK8vY5VqBA6hUlo1yuGvNA', help='Comment tag')
    p_ghost.add_argument('-q', '--quiet', action='store_true', help='Suppress progress messages')

    # Subcommand: inspect
    p_inspect = subparsers.add_parser('inspect', help='Inspect a local MP4 file')
    p_inspect.add_argument('file', help='Path to MP4 file')

    # Global flags
    parser.add_argument('-V', '--version', action='version', version=f'%(prog)s {__version__}')

    # Default fallback for backwards compatibility: "tiktok-quality input.mp4 output.mp4"
    if len(sys.argv) > 1 and sys.argv[1] not in ('patch', 'check', 'ghost', 'inspect', '-h', '--help', '-V', '--version'):
        # Inject 'patch' or 'ghost'
        sys.argv.insert(1, 'patch')

    args = parser.parse_args()

    if args.command == 'patch':
        if not Path(args.input).exists():
            print(f"[!] File not found: {args.input}", file=sys.stderr)
            sys.exit(1)
        try:
            report = patch_fps(
                in_file=args.input,
                out_file=args.output,
                fps_in=args.fps_in,
                fps_out=args.fps_out,
                mode=args.mode,
                include_audio=not args.no_audio,
            )
            for line in report:
                print(f"[*] {line}")
            print(f"[✓] Successfully wrote patched video to: {args.output}")
        except Exception as e:
            print(f"[!] ERROR: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == 'check':
        try:
            info = check_tiktok(args.url)
            print(f"[*] Author: @{info.get('author')}")
            print(f"[*] Title: {info.get('title')}")
            print(f"[*] Duration: {info.get('duration')}s")
            print(f"[*] Best FPS: {info.get('best_fps')} fps")
            print(f"[*] Verdict: {info.get('verdict')}")
            print("\nAvailable Streams:")
            for s in info.get('streams', []):
                print(f"  - {s.get('name')}: {s.get('width')}x{s.get('height')} @ {s.get('fps')}fps ({s.get('codec')}, {s.get('mbps')} Mbps)")
        except Exception as e:
            print(f"[!] ERROR: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == 'ghost':
        if not Path(args.input).exists():
            print(f"[!] File not found: {args.input}", file=sys.stderr)
            sys.exit(1)
        try:
            transform(
                input_path=args.input,
                output_path=args.output,
                multiplier=args.multiplier,
                comment=args.comment,
                verbose=not args.quiet,
            )
        except Exception as e:
            print(f"[!] ERROR: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == 'inspect':
        fps = detect_fps(args.file)
        print(f"[*] File: {args.file}")
        print(f"[*] Detected FPS: {fps}")

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
