import os
import sys
import json
import polib
import requests

def get_github_headers(token):
    """生成 GitHub API 請求標頭"""
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

def find_existing_suggestions(repo, pr_number, token):
    """查找 Bot 之前發布的建議 (Review Comments)"""
    headers = get_github_headers(token)
    existing_suggestions = set()
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/comments"
    
    # GitHub API 可能會分頁，這裡簡單處理第一頁，對於大多數 PR 已經足夠
    response = requests.get(url, headers=headers, params={"per_page": 100})
    
    if response.status_code == 200:
        for comment in response.json():
            # 確保只計算我們自己的 Bot 留下的建議
            if comment.get("user", {}).get("login") == "github-actions[bot]":
                path = comment.get("path")
                line = comment.get("line")
                if path and line:
                    existing_suggestions.add((path, line))
    return existing_suggestions

def post_suggestion(repo, pr_number, token, commit_id, path, line, body):
    """在 PR 的特定行上發表建議"""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/comments"
    payload = {
        "body": body,
        "commit_id": commit_id,
        "path": path,
        "line": line
    }
    response = requests.post(url, headers=get_github_headers(token), json=payload)
    if response.status_code == 201:
        print(f"Successfully posted suggestion for {path} at line {line}.")
    else:
        print(f"Failed to post suggestion for {path} at line {line}. Status: {response.status_code}, Response: {response.text}")

def check_po_file(file_path, glossary, existing_suggestions):
    """檢查單一 PO 檔案，返回所有需要提出的建議"""
    print(f"Checking file: {file_path}")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    po = polib.pofile(file_path, encoding='utf-8')
    suggestions_to_make = []

    for entry in po:
        if not entry.msgid or not entry.msgstr:
            continue

        if entry.msgid in glossary:
            term_data = glossary[entry.msgid]
            correct_translation = term_data['target']
            
            if entry.msgstr != correct_translation:
                # 尋找精確的 msgstr 行號
                msgstr_linenum = -1
                for i in range(entry.linenum - 1, len(lines)):
                    if lines[i].strip().startswith('msgstr'):
                        msgstr_linenum = i + 1
                        break
                
                if msgstr_linenum == -1:
                    continue

                # 檢查是否已對此行提出過建議
                if (file_path, msgstr_linenum) in existing_suggestions:
                    print(f"  Skipping already commented suggestion for '{entry.msgid}' at line {msgstr_linenum}.")
                    continue

                # 統一產生 Suggestion
                print(f"  [SUGGESTION] Found incorrect translation for '{entry.msgid}' at line {msgstr_linenum}.")
                
                # 根據是否為常見錯誤，客製化留言內容
                if entry.msgstr in term_data.get('errors', []):
                    reason = "是已知的常見錯誤"
                else:
                    reason = "不符合術語表規範"
                
                message_body = (
                    f"術語 `{entry.msgid}` 的翻譯 `{entry.msgstr}` {reason}。\n"
                    f"建議更正為 `{correct_translation}`。\n"
                    f"```suggestion\n"
                    f"{correct_translation}\n"
                    f"```"
                )
                
                suggestions_to_make.append({'path': file_path, 'line': msgstr_linenum, 'body': message_body})

    return suggestions_to_make

def load_glossary(file_path):
    """載入術語表"""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return {item['source']: {'target': item['target'], 'errors': item.get('common_errors', [])} for item in data}

if __name__ == "__main__":
    glossary_file = sys.argv[1]
    po_files = sys.argv[2:]

    github_token = os.environ.get("GITHUB_TOKEN")
    github_repo = os.environ.get("GITHUB_REPOSITORY")
    pr_number = os.environ.get("PR_NUMBER")
    commit_id = os.environ.get("COMMIT_ID")

    if not all([github_token, github_repo, pr_number, commit_id]):
        print("Error: Missing required environment variables.")
        sys.exit(1)

    # 1. 查找舊的建議，避免重複留言
    print("Finding existing suggestions...")
    existing_suggestions = find_existing_suggestions(github_repo, pr_number, github_token)
    print(f"Found {len(existing_suggestions)} existing suggestions.")

    # 2. 載入術語表並檢查檔案
    glossary = load_glossary(glossary_file)
    all_suggestions = []

    for po_file in po_files:
        if os.path.exists(po_file) and os.path.getsize(po_file) > 0:
            suggestions = check_po_file(po_file, glossary, existing_suggestions)
            all_suggestions.extend(suggestions)

    # 3. 發表所有新的 Suggestion
    for suggestion in all_suggestions:
        post_suggestion(github_repo, pr_number, github_token, commit_id, suggestion['path'], suggestion['line'], suggestion['body'])

    # 4. 根據結果決定 Action 狀態
    if all_suggestions:
        print(f"\nFound {len(all_suggestions)} issues. Exiting with status 1 to fail the check.")
        sys.exit(1)
    else:
        # 如果沒有新問題，我們還需要檢查是否需要清理舊的建議
        # 為了簡化，目前版本不處理自動解決/刪除舊建議的邏輯。
        # 當使用者接受建議或手動修復後，下一次運行時，這些問題自然不會再被提出。
        print("\nNo new issues found. All good!")
        sys.exit(0)
        