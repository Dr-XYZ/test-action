import os
import sys
import json
import polib
import requests

def load_glossary(file_path):
    """載入術語表並轉換成方便查找的格式"""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    glossary_map = {item['source']: {
        'target': item['target'],
        'errors': item.get('common_errors', [])
    } for item in data}
    return glossary_map

def post_review_comment(repo, pr_number, token, commit_id, path, line, body):
    """在 PR 的特定行上發表評論 (用於 Suggestion)"""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/comments"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    payload = {
        "body": body,
        "commit_id": commit_id,
        "path": path,
        "line": line
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 201:
        print(f"Successfully posted suggestion for {path} at line {line}.")
    else:
        print(f"Failed to post suggestion for {path} at line {line}. Status: {response.status_code}, Response: {response.text}")
    return response.status_code == 201

def check_po_file(file_path, glossary, repo, pr_number, token, commit_id):
    """檢查單一 PO 檔案"""
    print(f"Checking file: {file_path}")
    po = polib.pofile(file_path, encoding='utf-8')
    found_issues = []

    for entry in po:
        if not entry.msgid or not entry.msgstr:
            continue

        if entry.msgid in glossary:
            term_data = glossary[entry.msgid]
            correct_translation = term_data['target']
            common_errors = term_data['errors']

            if entry.msgstr != correct_translation:
                if entry.msgstr in common_errors:
                    print(f"  [SUGGESTION] Found common error for '{entry.msgid}' at line {entry.linenum}.")
                    print(f"    - Incorrect: '{entry.msgstr}'")
                    print(f"    - Correct: '{correct_translation}'")
                    
                    message_body = (
                        f"術語 `{entry.msgid}` 的翻譯 `{entry.msgstr}` 是已知的常見錯誤。\n"
                        f"建議更正為 `{correct_translation}`。\n"
                        f"```suggestion\n"
                        f"{correct_translation}\n"
                        f"```"
                    )
                    
                    post_review_comment(repo, pr_number, token, commit_id, file_path, entry.linenum, message_body)
                    found_issues.append(f"Suggestion for {file_path}:{entry.linenum}")

                else:
                    print(f"  [COMMENT] Found incorrect translation for '{entry.msgid}' at line {entry.linenum}.")
                    print(f"    - Current: '{entry.msgstr}'")
                    print(f"    - Recommended: '{correct_translation}'")
                    
                    issue_detail = (
                        f"- **檔案**: `{file_path}` (第 {entry.linenum} 行)\n"
                        f"  - **原文**: `{entry.msgid}`\n"
                        f"  - **目前翻譯**: `{entry.msgstr}`\n"
                        f"  - **建議翻譯**: `{correct_translation}`"
                    )
                    found_issues.append(issue_detail)

    return found_issues

def post_summary_comment(repo, pr_number, token, issues):
    """將所有非 Suggestion 的問題整理成一則留言"""
    if not issues:
        return

    comment_body = "### 術語表檢查報告\n\n我發現一些翻譯可能需要根據術語表進行調整：\n\n" + "\n".join(issues)
    
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    payload = {"body": comment_body}
    response = requests.post(url, headers=headers, json=payload)
    
    if response.status_code == 201:
        print("Successfully posted summary comment.")
    else:
        print(f"Failed to post summary comment. Status: {response.status_code}, Response: {response.text}")

if __name__ == "__main__":
    glossary_file = sys.argv[1]
    po_files = sys.argv[2:]

    github_token = os.environ.get("GITHUB_TOKEN")
    github_repo = os.environ.get("GITHUB_REPOSITORY")
    pr_number = os.environ.get("PR_NUMBER")
    commit_id = os.environ.get("COMMIT_ID")

    if not all([github_token, github_repo, pr_number, commit_id]):
        print("Error: Missing required environment variables (GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER, COMMIT_ID).")
        sys.exit(1)

    print("Loading glossary...")
    glossary = load_glossary(glossary_file)
    
    all_issues = []
    suggestion_issues = []

    for po_file in po_files:
        if os.path.exists(po_file) and os.path.getsize(po_file) > 0:
            issues = check_po_file(po_file, glossary, github_repo, pr_number, github_token, commit_id)
            for issue in issues:
                if issue.startswith("Suggestion"):
                    suggestion_issues.append(issue)
                else:
                    all_issues.append(issue)

    if all_issues:
        # *** 這就是修正的地方 ***
        post_summary_comment(github_repo, pr_number, github_token, all_issues)

    if all_issues or suggestion_issues:
        print("\nFound issues. Exiting with status 1 to fail the check.")
        sys.exit(1)
    else:
        print("\nNo issues found. All good!")
        sys.exit(0)