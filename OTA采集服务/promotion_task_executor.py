from __future__ import annotations

import argparse

import promotion_controls
import ctrip_promotion_controls
import promotion_queue
import runner


def run(max_tasks: int) -> int:
    from update_guard import ensure_not_updating
    ensure_not_updating()
    settings = runner.load_settings()
    failed = 0
    remaining = max(1, max_tasks)
    for platform, controller in (('meituan', promotion_controls), ('ctrip', ctrip_promotion_controls)):
        if not (settings.get(platform) or {}).get('enabled', True) or not runner.internal_hotel_id(settings, platform):
            continue
        promotion_queue.fail_interrupted(settings, platform)
        if remaining <= 0:
            continue
        tasks = promotion_queue.claim(settings, platform, remaining)
        remaining -= len(tasks)
        for task in tasks:
            try:
                promotion = controller.find_promotion(settings, task['launch_id'])
                message = controller.control_promotion(settings, promotion, task['action'])
                promotion_queue.finish(settings, platform, task['id'], 'success')
                print(f"{platform} promotion task {task['id']} completed: {message}")
            except Exception as exc:
                failed += 1
                error = runner.sanitize(str(exc), settings)
                promotion_queue.finish(settings, platform, task['id'], 'failed', error)
                print(f"{platform} promotion task {task['id']} failed: {error}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Process pending Meituan and Ctrip promotion controls.")
    parser.add_argument("--max-tasks", type=int, default=5)
    args = parser.parse_args()
    return run(max(1, args.max_tasks))


if __name__ == "__main__":
    raise SystemExit(main())
