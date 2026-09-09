"""Generate Phase 3 comment drafts from a saved Phase 2 profile report.

Load extracted written prompts, request three OpenAI-generated options, validate
their targets and content structure, and print them alongside their source text.
Save unapproved drafts with source provenance under captures/. --dry-run saves
the exact request for offline inspection without calling OpenAI. No emulator or
Hinge submission code runs here.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from comment_drafts import DEFAULT_TONE, PROMPT_VERSION, build_request, prompt_items, validate_drafts
from llm_client import generate_json, load_project_env


def run(profile_path, model, *, item_id=None, tone=DEFAULT_TONE, max_chars=180,
        about_me="", output_root="captures", dry_run=False, client=None):
    source = Path(profile_path)
    if source.stat().st_size > 5_000_000:
        raise ValueError("Profile report is unexpectedly large (over 5 MB).")
    source_bytes = source.read_bytes()
    report = json.loads(source_bytes)
    items = prompt_items(report, item_id)
    request = build_request(items, tone, max_chars, about_me)
    if model == "gpt-5.6-sol":
        # Short creative drafts do not need a separate reasoning-token budget.
        request["reasoning"] = {"effort": "none"}
    if len(request["input"]) > 40_000 or len(request["instructions"]) > 10_000:
        raise ValueError("Prompt input is too large; reduce profile items or writing preferences.")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Specify a nonempty model name.")
    payload = {"schema_version": 1, "prompt_version": PROMPT_VERSION,
               "created_at": datetime.now(timezone.utc).isoformat(),
               "source_profile": str(source.resolve()), "source_scan_id": report["scan_id"],
               "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
               "requested_model": model, "settings": {"tone": tone, "max_chars": max_chars},
               "length_limit_source": "user_draft_budget_not_verified_hinge_limit",
               "items": items, "approved": False, "requires_fresh_resolution": True}
    if dry_run:
        payload.update({"status": "request_preview_only", "request": request})
        name = "request.json"
    else:
        result, metadata = generate_json(request, model, client=client)
        payload.update({"status": "drafts_pending_review",
                        "drafts": validate_drafts(result, items, max_chars), "generation": metadata})
        name = "drafts.json"
    folder = Path(output_root) / ("drafts_" + uuid.uuid4().hex)
    folder.mkdir(parents=True, mode=0o700)
    target = folder / name
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if dry_run:
        print(f"Request preview saved: {target.resolve()} (no API call)")
    else:
        by_id = {item["item_id"]: item for item in items}
        drafts = payload["drafts"]
        for candidate in drafts["candidates"]:
            item = by_id[candidate["item_id"]]
            recommended = " [recommended]" if candidate["candidate_id"] == drafts["recommended_candidate_id"] else ""
            print(f"\n{candidate['candidate_id']}{recommended} — {item['title']}")
            print(f"Response: {item['response']}\nComment: {candidate['comment']}")
        print(f"\nRecommendation: {drafts['recommendation_reason']}")
        print(f"\nUnapproved drafts saved: {target.resolve()}")
    return target


def main():
    try:
        load_project_env()
    except ImportError:
        print("Install dependencies: python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path, help="Phase 2 profile.json")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5.6-sol"))
    parser.add_argument("--item-id", help="Generate all three options for a specific extracted prompt")
    parser.add_argument("--tone", default=DEFAULT_TONE)
    parser.add_argument("--max-chars", type=int, default=180, help="Draft budget, not a verified Hinge limit")
    parser.add_argument("--about-me", type=Path, help="Optional UTF-8 file of your own facts for personalization")
    parser.add_argument("--output", default="captures")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        about_me = args.about_me.read_text(encoding="utf-8") if args.about_me else ""
        run(args.profile, args.model, item_id=args.item_id, tone=args.tone,
            max_chars=args.max_chars, about_me=about_me, output_root=args.output, dry_run=args.dry_run)
        return 0
    except (ValueError, OSError) as exc:
        print(f"Draft generation stopped: {exc}", file=sys.stderr)
        return 1
    except ImportError:
        print("Install dependencies: python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Draft generation cancelled.", file=sys.stderr)
        return 1
    except Exception as exc:
        # SDK errors can contain request/response data; keep private content out of logs.
        print(f"OpenAI request failed ({type(exc).__name__}). Check credentials, model access, and connectivity.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
