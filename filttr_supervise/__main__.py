"""CLI entry: ``python -m filttr_supervise``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Filttr Supervise — USB iPhone supervision for Filttr MDM")
    parser.add_argument("--config", type=Path, help="Path to config.json")
    parser.add_argument("--gui", action="store_true", help="Launch tkinter GUI (default if no action)")
    parser.add_argument("--user", action="store_true", help="Launch end-user setup GUI (setup-code only, no admin login)")
    parser.add_argument("--supervise", action="store_true", help="Run supervise flow on connected device")
    parser.add_argument("--remove", action="store_true", help="Run remove-supervision flow")
    parser.add_argument(
        "--dry-run",
        type=Path,
        metavar="DIR",
        help="Build backup package into DIR without a device",
    )
    parser.add_argument("--udid", help="Target device UDID")
    parser.add_argument("--access-key", help="Filttr portal access key (k=)")
    parser.add_argument("--info", action="store_true", help="Print connected device info and exit")
    args = parser.parse_args(argv)

    from filttr_supervise.config import load_config

    config = load_config(args.config)
    if getattr(args, "access_key", None):
        config.access_key = args.access_key

    if args.info:
        from filttr_supervise.device import get_device_info

        info = get_device_info(args.udid)
        print(info.to_dict())
        return 0 if info.connected else 1

    if args.dry_run:
        from filttr_supervise.supervise import dry_run_build_backup

        path = dry_run_build_backup(
            args.dry_run, config, udid=args.udid or "DRYRUN-UDID", remove=args.remove
        )
        print(f"Dry-run backup written to: {path}")
        return 0

    if args.supervise or args.remove:
        from filttr_supervise.supervise import supervise_device

        def progress(line: str) -> None:
            print(line)

        result = supervise_device(
            config,
            remove=args.remove,
            progress=progress,
            udid=args.udid,
        )
        return 0 if result.ok else 2

    if args.user:
        from filttr_supervise.user_gui import run_user_gui

        run_user_gui(config)
        return 0

    # Default: admin GUI
    from filttr_supervise.gui import run_gui

    run_gui(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
