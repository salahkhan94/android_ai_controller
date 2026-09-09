"""Run one text-profile workflow with mandatory approval before sending.

Scan the currently open Hinge profile, generate three comment drafts, and show
the recommended target and exact comment. The user may choose another candidate
or cancel. Only explicit approval proceeds to fresh profile verification,
composer preparation, and one submission transaction. Save an audit trail and
stop after this profile; never retry an uncertain send or run unattended.
"""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from comment_drafts import DEFAULT_TONE
from extract_profile import scan_profile
from generate_comments import run as generate_drafts
from llm_client import load_project_env
from prepare_comment import SEND_LABELS, load_selection, prepare
from print_profile_prompts import read_profile
from submit_comment import attempt_key, submit


PROJECT_ROOT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def close_driver(driver):
    try:
        driver.quit()
    except Exception:
        print("Could not cleanly close the Appium session.", file=sys.stderr)


def ask_approval(drafts_path, ask=input):
    """Approval binds to the displayed candidate and the exact saved draft bytes."""
    original_hash = digest(drafts_path)
    candidate_id = "recommended"
    while True:
        if digest(drafts_path) != original_hash:
            raise RuntimeError("Draft file changed during review. Start a fresh run.")
        profile, target, candidate = load_selection(drafts_path, candidate_id)
        print(f"\nProfile: {profile['profile_label'].removeprefix('Skip ')}")
        print(f"Prompt: {target['title']}\nTheir response: {target['response']}")
        print(f"\nSelected comment ({candidate['candidate_id']}):\n{candidate['comment']}")
        print("\nApproval sends this comment with a standard like, not a Rose.")
        try:
            answer = ask("Type SEND to approve, c1/c2/c3 to review another option, or Enter to cancel: ").strip()
        except EOFError:
            return None
        if answer in {"c1", "c2", "c3"}:
            candidate_id = answer
            continue
        if answer.lower() not in {"send", "yes"}:
            return None
        if digest(drafts_path) != original_hash:
            raise RuntimeError("Draft file changed after display; approval discarded.")
        return {"candidate_id": candidate["candidate_id"], "item_id": target["item_id"],
                "comment": candidate["comment"], "title": target["title"], "response": target["response"],
                "profile_label": profile["profile_label"], "source_scan_id": profile["scan_id"],
                "drafts_sha256": original_hash, "approved_at": datetime.now(timezone.utc).isoformat()}


def pipeline(connect, *, model, output_root=PROJECT_ROOT / "captures", max_scrolls=30,
             tone=DEFAULT_TONE, max_chars=180, about_me="", ask=input):
    output_root = Path(output_root).resolve()
    run_dir = output_root / ("run_" + uuid.uuid4().hex)
    run_dir.mkdir(parents=True, mode=0o700)
    record = {"schema_version": 1, "status": "started", "approval": None}
    try:
        print("Scanning the current profile. Keep the emulator untouched while it scrolls.", flush=True)
        driver = connect()
        try:
            root, _ = read_profile(driver)
            if any(n.get("content-desc") == "Edit comment" or n.get("content-desc") in SEND_LABELS
                   for n in root.iter()):
                raise RuntimeError("A comment composer is already open. Close it manually before starting run.py.")
            profile = scan_profile(driver, output_root, max_scrolls)
        finally:
            close_driver(driver)
        profile_path = output_root / profile["scan_id"] / "profile.json"
        record["profile_path"] = str(profile_path)
        print("Generating comment options…", flush=True)
        drafts_path = generate_drafts(profile_path, model, tone=tone, max_chars=max_chars,
                                      about_me=about_me, output_root=output_root)
        record["drafts_path"] = str(drafts_path)
        # No device session is held while the user reviews; approval can take time.
        approval = ask_approval(drafts_path, ask)
        if approval is None:
            record["status"] = "cancelled_without_sending"
            print("Cancelled. Drafts are saved; no comment was entered or sent.")
            return record
        record["approval"] = approval
        record["status"] = "approved_pending_reverification"
        (run_dir / "run.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        if digest(drafts_path) != approval["drafts_sha256"]:
            raise RuntimeError("Approved drafts changed. Start a fresh run.")
        source, target, _ = load_selection(drafts_path, approval["candidate_id"])
        if (output_root / "submissions" / (attempt_key(source, target) + ".json")).exists():
            raise RuntimeError("A send attempt already exists for this target. Do not retry it.")
        print("Approved. Rechecking the profile, preparing the comment, then submitting once…", flush=True)
        driver = connect()
        try:
            prepared = prepare(driver, drafts_path, approval["candidate_id"],
                               max_scrolls=max_scrolls, output_root=output_root)
            record["preparation_path"] = prepared["preparation_path"]
            if digest(drafts_path) != approval["drafts_sha256"] or (
                    prepared["candidate_id"], prepared["item_id"], prepared["comment"], prepared["source_scan_id"]) != (
                    approval["candidate_id"], approval["item_id"], approval["comment"], approval["source_scan_id"]):
                raise RuntimeError("Prepared comment does not match approval. Nothing submitted.")
            submitted = submit(driver, prepared["preparation_path"], send=True, output_root=output_root)
            record["submission"] = submitted
            record["status"] = submitted["status"]
            if record["status"] == "confirmed_by_ui":
                print("Hinge displayed a success confirmation.")
            else:
                print("Submission outcome is uncertain. Do not retry; inspect the saved evidence.")
            return record
        finally:
            close_driver(driver)
    except (KeyboardInterrupt, EOFError):
        record["status"] = "interrupted_check_submission_ledger" if record["approval"] else "cancelled_without_sending"
        raise
    except Exception as exc:
        record["status"] = "stopped_check_submission_ledger"
        record["error_type"] = type(exc).__name__
        raise
    finally:
        (run_dir / "run.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Run record: {run_dir / 'run.json'}")


def main():
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:4723")
    parser.add_argument("--udid", default="127.0.0.1:6555")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5.6-sol"))
    parser.add_argument("--max-scrolls", type=int, default=30)
    parser.add_argument("--tone", default=DEFAULT_TONE)
    parser.add_argument("--max-chars", type=int, default=180)
    parser.add_argument("--about-me", type=Path)
    args = parser.parse_args()
    if args.max_scrolls < 2 or not 40 <= args.max_chars <= 1000:
        parser.error("Use --max-scrolls >= 2 and --max-chars between 40 and 1000.")
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error("Add OPENAI_API_KEY to the project .env file first.")
    output_root = PROJECT_ROOT / "captures"
    output_root.mkdir(exist_ok=True)
    # One runner at a time for the same device, including the approval interval.
    lock_name = hashlib.sha256(args.udid.encode()).hexdigest()
    with (output_root / ("runner_" + lock_name + ".lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another run.py process is already using this device.", file=sys.stderr)
            return 1

        def connect():
            from appium import webdriver
            from appium.options.android import UiAutomator2Options
            options = UiAutomator2Options().load_capabilities({"platformName": "Android",
                "appium:automationName": "UiAutomator2", "appium:udid": args.udid,
                "appium:noReset": True, "appium:autoLaunch": False})
            return webdriver.Remote(args.server, options=options)

        try:
            about_me = args.about_me.read_text(encoding="utf-8") if args.about_me else ""
            result = pipeline(connect, model=args.model, output_root=output_root,
                              max_scrolls=args.max_scrolls, tone=args.tone,
                              max_chars=args.max_chars, about_me=about_me)
            return 0 if result["status"] in {"confirmed_by_ui", "cancelled_without_sending"} else 2
        except (KeyboardInterrupt, EOFError):
            print("Stopped. If submission had started, inspect its ledger before retrying.")
            return 1
        except Exception as exc:
            print(f"Pipeline stopped ({type(exc).__name__}). No automatic retry. Check the run record and submission ledger.", file=sys.stderr)
            if isinstance(exc, (ValueError, RuntimeError)):
                print(str(exc), file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
