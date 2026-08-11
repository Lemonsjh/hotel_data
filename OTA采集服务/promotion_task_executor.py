from __future__ import annotations

import argparse

import promotion_controls
import runner


def run(max_tasks: int) -> int:
    settings = runner.load_settings()
    promotion_controls.fail_interrupted_control_tasks(settings)
    tasks = promotion_controls.claim_control_tasks(settings, max_tasks)
    if not tasks:
        print("No pending Meituan promotion control tasks")
        return 0

    failed = 0
    for task in tasks:
        try:
            promotion = promotion_controls.find_promotion(settings, task["launch_id"])
            message = promotion_controls.control_promotion(settings, promotion, task["action"])
            promotion_controls.finish_control_task(settings, task["id"], "success")
            print(f"Promotion task {task['id']} completed: {message}")
        except Exception as exc:
            failed += 1
            promotion_controls.finish_control_task(settings, task["id"], "failed", str(exc))
            print(f"Promotion task {task['id']} failed: {exc}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Process pending Meituan promotion control tasks.")
    parser.add_argument("--max-tasks", type=int, default=5)
    args = parser.parse_args()
    return run(max(1, args.max_tasks))


if __name__ == "__main__":
    raise SystemExit(main())
