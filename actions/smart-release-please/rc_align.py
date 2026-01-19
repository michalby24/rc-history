import os
import re
import subprocess
import sys

BOT_COMMIT_MSG = "chore: enforce correct rc version"
BOT_FOOTER_TAG = "Release-As:"

def run_git_command(args, fail_on_error=True):
    try:
        result = subprocess.run(["git"] + args, stdout=subprocess.PIPE, text=True, check=fail_on_error)
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None

def parse_semver(tag):
    if not tag: return 0, 0, 0, 0
    m_rc = re.match(r"^v(\d+)\.(\d+)\.(\d+)-rc\.(\d+)$", tag)
    if m_rc: return int(m_rc[1]), int(m_rc[2]), int(m_rc[3]), int(m_rc[4])
    m_stable = re.match(r"^v(\d+)\.(\d+)\.(\d+)$", tag)
    if m_stable: return int(m_stable[1]), int(m_stable[2]), int(m_stable[3]), 0
    return 0, 0, 0, 0

def find_baseline_tag():
    # Fetch ALL tags reachable from HEAD
    tags_output = run_git_command(["tag", "-l", "v*", "--merged", "HEAD"], fail_on_error=False)
    if not tags_output: return None, True
    
    all_tags = tags_output.split('\n')
    
    # Python Sort: Ensures Stable > RC (e.g. 0.1.1 > 0.1.1-rc.99)
    def version_key(t):
        maj, min, pat, rc = parse_semver(t)
        is_stable = 1 if "-rc" not in t else 0
        return (maj, min, pat, is_stable, rc)

    best_tag = sorted(all_tags, key=version_key, reverse=True)[0]
    
    if "-rc" in best_tag: return best_tag, False
    return best_tag, True

def get_commit_depth(baseline_tag):
    rev_range = f"{baseline_tag}..HEAD" if baseline_tag else "HEAD"
    
    # Use format=%s (subject) to filter
    raw_subjects = run_git_command(["log", rev_range, "--first-parent", "--pretty=format:%s"], fail_on_error=False)
    if not raw_subjects: return 0

    real_commits = []
    for s in raw_subjects.split('\n'):
        # 1. Skip Bot/Release commits
        if BOT_FOOTER_TAG in s or BOT_COMMIT_MSG in s: continue
        if re.match(r"^chore(\(.*\))?: release", s): continue
        if "chore: reset manifest" in s: continue
        
        # 2. Skip Sync Merges (Main -> Next)
        # Prevents version bump just for syncing stable changes back to RC
        if "Merge branch" in s and ("main" in s or "master" in s):
            print(f"INFO: Ignoring sync merge commit: {s}")
            continue

        real_commits.append(s)
    
    return len(real_commits)

def analyze_impact(baseline_tag):
    rev_range = f"{baseline_tag}..HEAD" if baseline_tag else "HEAD"
    logs = run_git_command(["log", rev_range, "--pretty=format:%B"], fail_on_error=False)
    if not logs: return False, False
    breaking_regex = r"^(feat|fix|refactor)(\(.*\))?!:"
    is_breaking = re.search(breaking_regex, logs, re.MULTILINE) or "BREAKING CHANGE" in logs
    is_feat = re.search(r"^feat(\(.*\))?:", logs, re.MULTILINE)
    return bool(is_breaking), bool(is_feat)

def calculate_next_version(major, minor, patch, rc, depth, is_breaking, is_feat, from_stable):
    if is_breaking: return f"{major + 1}.0.0-rc.{depth}"
    if is_feat:
        if from_stable or patch > 0: return f"{major}.{minor + 1}.0-rc.{depth}"
        else: return f"{major}.{minor}.{patch}-rc.{rc + depth}"
    if from_stable: return f"{major}.{minor}.{patch + 1}-rc.{depth}"
    else: return f"{major}.{minor}.{patch}-rc.{rc + depth}"

def main():
    branch = os.environ.get("GITHUB_REF_NAME")
    
    # --- SAFETY: SKIP MAIN ---
    # We do NOT want to calculate or force versions on main.
    # We trust the manifest merged from next.
    if branch in ["main", "master"]:
        print("INFO: Running on main/master. Skipping RC calculation.")
        return

    # --- LOGIC FOR NEXT ---
    try:
        tag, from_stable = find_baseline_tag()
        depth = get_commit_depth(tag)
        
        if depth == 0:
            print("INFO: No user commits found since baseline. Exiting.")
            return

        major, minor, patch, rc = parse_semver(tag)
        is_breaking, is_feat = analyze_impact(tag)

        next_ver = calculate_next_version(major, minor, patch, rc, depth, is_breaking, is_feat, from_stable)

        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"next_version={next_ver}\n")
        print(f"OUTPUT: next_version={next_ver}")

    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        sys.exit(0)

if __name__ == "__main__":
    main()
