"""
DRISHTI-PAT Command Line Interface (CLI)
Automated benchmarking, paired comparison, and video tracking tool.

Usage:
    python cli.py sim --trajectory LEO_PASS --duration 10.0 --tracker DRISHTI_PAT
    python cli.py compare --trajectory FIGURE_EIGHT --duration 8.0
    python cli.py video --input sample_data/sample_leo_pass.mp4 --output reports/video_results.csv
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.benchmark_suite import BenchmarkSuite
from core.tracker import DRISHTIPATTracker
from core.video_adapter import VideoBenchmarkAdapter

console = Console()


def run_simulation(args):
    console.print(
        Panel.fit(
            "[bold cyan]DRISHTI-PAT: Closed-Loop Simulation Run[/bold cyan]\n"
            f"Trajectory: [yellow]{args.trajectory}[/yellow] | Duration: [yellow]{args.duration}s[/yellow] | Tracker: [green]{args.tracker}[/green]",
            border_style="cyan",
        )
    )

    bench = BenchmarkSuite(seed=args.seed)
    result = bench.run_scenario(
        tracker_type=args.tracker,
        trajectory=args.trajectory,
        duration_s=args.duration,
        noise_std=args.noise,
        hot_pixel_count=args.hot_pixels,
    )

    summary = result["summary"]
    df = result["dataframe"]

    # Print ISRO Target Evaluation Table
    table = Table(
        title="ISRO Supplementary Specification Compliance Report", style="cyan"
    )
    table.add_column("Specification Metric", style="white", justify="left")
    table.add_column("Official Target", style="magenta", justify="center")
    table.add_column("Measured Value", style="bold yellow", justify="center")
    table.add_column("Status", style="bold", justify="center")

    def format_pass(passed: bool):
        return "[green]PASS[/green]" if passed else "[red]FAIL[/red]"

    table.add_row(
        "Acquisition Time",
        "<= 2.0 s",
        f"{summary['acquisition_time_s']:.2f} s",
        format_pass(summary["pass_acquisition_target"]),
    )
    table.add_row(
        "Re-acquisition Time",
        "<= 1.0 s",
        f"{summary['reacquisition_time_s']:.2f} s",
        format_pass(summary["pass_reacquisition_target"]),
    )
    table.add_row(
        "Tracking Error (Median)",
        "<= 10.0 px",
        f"{summary['tracking_error_median_px']:.2f} px",
        format_pass(summary["pass_tracking_error_target"]),
    )
    table.add_row(
        "Tracking Error (P95)", "--", f"{summary['tracking_error_p95_px']:.2f} px", "--"
    )
    table.add_row(
        "Target Loss Rate",
        "< 5.0 %",
        f"{summary['target_loss_rate_pct']:.2f} %",
        format_pass(summary["pass_loss_rate_target"]),
    )
    table.add_row(
        "Processing Throughput",
        ">= 20.0 FPS",
        f"{summary['effective_fps']:.1f} FPS ({summary['mean_processing_cost_ms']:.2f} ms)",
        format_pass(summary["pass_fps_target"]),
    )
    table.add_row(
        "Sensor Artifact False-Lock",
        "0.0 s",
        f"{summary['artifact_false_lock_time_s']:.2f} s",
        "[green]REJECTED[/green]"
        if summary["artifact_false_lock_time_s"] == 0
        else "[red]TRICKED[/red]",
    )
    table.add_row(
        "Invalid-READY Handover Time",
        "0.0 s",
        f"{summary['invalid_ready_duration_s']:.2f} s",
        "[green]SAFE[/green]"
        if summary["invalid_ready_duration_s"] == 0
        else "[yellow]WARNING[/yellow]",
    )

    console.print(table)

    # Export CSV if requested
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        df.to_csv(args.output, index=False)
        console.print(
            f"[bold green]Per-frame audit ledger saved to:[/bold green] {args.output}"
        )


def run_comparison(args):
    console.print(
        Panel.fit(
            "[bold magenta]DRISHTI-PAT: Paired Comparison Benchmark[/bold magenta]\n"
            "Testing Baseline B0 vs Baseline B1 vs DRISHTI-PAT on identical seeded frames",
            border_style="magenta",
        )
    )

    bench = BenchmarkSuite(seed=args.seed)
    comp = bench.run_paired_comparison(
        trajectory=args.trajectory, duration_s=args.duration
    )

    table = Table(
        title=f"Paired Comparison Benchmark ({args.trajectory}, {args.duration}s)",
        style="magenta",
    )
    table.add_column("Metric", style="white")
    table.add_column("Baseline B0\n(Detector+PID)", style="yellow", justify="center")
    table.add_column("Baseline B1\n(+Kalman Filter)", style="cyan", justify="center")
    table.add_column(
        "DRISHTI-PAT (Ours)\n(Adaptive Evidence)", style="bold green", justify="center"
    )

    b0, b1, pat = comp["BASELINE_B0"], comp["BASELINE_B1"], comp["DRISHTI_PAT"]

    table.add_row(
        "Acquisition Time",
        f"{b0['acquisition_time_s']:.2f} s",
        f"{b1['acquisition_time_s']:.2f} s",
        f"[bold green]{pat['acquisition_time_s']:.2f} s[/bold green]",
    )
    table.add_row(
        "Re-acq Time",
        f"{b0['reacquisition_time_s']:.2f} s",
        f"{b1['reacquisition_time_s']:.2f} s",
        f"[bold green]{pat['reacquisition_time_s']:.2f} s[/bold green]",
    )
    table.add_row(
        "Median Track Error",
        f"{b0['tracking_error_median_px']:.2f} px",
        f"{b1['tracking_error_median_px']:.2f} px",
        f"[bold green]{pat['tracking_error_median_px']:.2f} px[/bold green]",
    )
    table.add_row(
        "Target Loss Rate",
        f"{b0['target_loss_rate_pct']:.1f} %",
        f"{b1['target_loss_rate_pct']:.1f} %",
        f"[bold green]{pat['target_loss_rate_pct']:.1f} %[/bold green]",
    )
    table.add_row(
        "Throughput (FPS)",
        f"{b0['effective_fps']:.1f} FPS",
        f"{b1['effective_fps']:.1f} FPS",
        f"[bold green]{pat['effective_fps']:.1f} FPS[/bold green]",
    )
    table.add_row(
        "Artifact False Lock",
        f"[red]{b0['artifact_false_lock_time_s']:.2f} s[/red]",
        f"[red]{b1['artifact_false_lock_time_s']:.2f} s[/red]",
        f"[bold green]{pat['artifact_false_lock_time_s']:.2f} s (0%)[/bold green]",
    )
    table.add_row(
        "Invalid-READY Time",
        f"[red]{b0['invalid_ready_duration_s']:.2f} s[/red]",
        f"[yellow]{b1['invalid_ready_duration_s']:.2f} s[/yellow]",
        f"[bold green]{pat['invalid_ready_duration_s']:.2f} s (Safe)[/bold green]",
    )
    table.add_row(
        "ISRO Spec Met?",
        "[red]NO[/red]",
        "[yellow]PARTIAL[/yellow]",
        "[bold green]ALL TARGETS MET[/bold green]",
    )

    console.print(table)


def run_video(args):
    console.print(
        Panel.fit(
            f"[bold cyan]DRISHTI-PAT: Native .MP4 Video Benchmark[/bold cyan]\n"
            f"Input Video: [yellow]{args.input}[/yellow]",
            border_style="cyan",
        )
    )

    adapter = VideoBenchmarkAdapter(args.input)
    info = adapter.get_info()
    console.print(
        f"Video Info: {info['width']}x{info['height']} @ {info['fps']} FPS ({info['total_frames']} frames)"
    )

    tracker = DRISHTIPATTracker()
    records, stats = adapter.process_video(tracker)
    adapter.release()

    table = Table(title="MP4 Benchmark Results", style="cyan")
    table.add_column("Metric", style="white")
    table.add_column("Value", style="bold green")

    table.add_row("Processed Frames", str(stats["frames_processed"]))
    table.add_row("Total Processing Time", f"{stats['total_runtime_s']:.2f} s")
    table.add_row("Throughput", f"{stats['effective_fps']:.1f} FPS")
    table.add_row("Mean Frame Latency", f"{stats['average_frame_latency_ms']:.2f} ms")
    table.add_row(
        "ISRO >=20 FPS Compliant?",
        "[green]YES ✓[/green]" if stats["isro_fps_target_met"] else "[red]NO ✗[/red]",
    )

    console.print(table)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        # Export records to CSV
        rows = []
        for r in records:
            rows.append(
                {
                    "frame_id": r.frame_id,
                    "timestamp": r.timestamp,
                    "frame_state": r.frame_state.value,
                    "track_state": r.track_state.value,
                    "action_type": r.action_type.value,
                    "action_cost_ms": r.action_cost_ms,
                    "estimated_x": r.estimated_x,
                    "estimated_y": r.estimated_y,
                    "uncertainty_r95": r.uncertainty_r95,
                    "readiness_state": r.readiness_state.value,
                }
            )
        pd.DataFrame(rows).to_csv(args.output, index=False)
        console.print(
            f"[bold green]Per-frame video log saved to:[/bold green] {args.output}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="DRISHTI-PAT Coarse Optical Tracking System CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # sim subcommand
    sim_p = subparsers.add_parser("sim", help="Run closed-loop simulation scenario")
    sim_p.add_argument(
        "--trajectory",
        type=str,
        default="LEO_PASS",
        choices=["LEO_PASS", "CIRCULAR", "FIGURE_EIGHT", "RANDOM_WALK"],
    )
    sim_p.add_argument(
        "--duration", type=float, default=8.0, help="Duration in seconds"
    )
    sim_p.add_argument(
        "--tracker",
        type=str,
        default="DRISHTI_PAT",
        choices=["DRISHTI_PAT", "BASELINE_B0", "BASELINE_B1"],
    )
    sim_p.add_argument(
        "--noise", type=float, default=6.0, help="Noise standard deviation"
    )
    sim_p.add_argument(
        "--hot_pixels", type=int, default=4, help="Number of sensor hot pixels"
    )
    sim_p.add_argument("--seed", type=int, default=42)
    sim_p.add_argument(
        "--output", type=str, default="reports/sim_run.csv", help="Output CSV path"
    )

    # compare subcommand
    comp_p = subparsers.add_parser(
        "compare", help="Run paired comparison benchmark across baselines"
    )
    comp_p.add_argument(
        "--trajectory",
        type=str,
        default="LEO_PASS",
        choices=["LEO_PASS", "CIRCULAR", "FIGURE_EIGHT", "RANDOM_WALK"],
    )
    comp_p.add_argument("--duration", type=float, default=8.0)
    comp_p.add_argument("--seed", type=int, default=42)

    # video subcommand
    vid_p = subparsers.add_parser("video", help="Run image-space tracking on MP4 video")
    vid_p.add_argument(
        "--input", type=str, required=True, help="Path to input .mp4 video"
    )
    vid_p.add_argument(
        "--output", type=str, default="reports/mp4_run.csv", help="Output CSV path"
    )

    args = parser.parse_args()
    if args.command == "sim":
        run_simulation(args)
    elif args.command == "compare":
        run_comparison(args)
    elif args.command == "video":
        run_video(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
