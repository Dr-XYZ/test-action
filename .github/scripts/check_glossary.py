import os
import sys
import json
import polib
import requests
import re

def get_github_headers(token):
    """生成 GitHub API 請求標頭"""
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

def find_existing_comments(repo, pr_number, token):
    """查找 Bot 之前發布的評論，避免重複"""
    headers = get_github_headers(token)
    existing_comments = set()
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/comments"
    response = requests.get(url, headers=headers, params={"per_page": 100})
    
    if response.status_code == 200:
        for comment in response.json():
            if comment.get("user", {}).get("login") == "github-actions[bot]":
                path = comment.get("path")
                line = comment.get("line")
                if path and line:
                    existing_comments.add((path, line))
    return existing_comments

def post_line_comment(repo, pr_number, token, commit_id, path, line, body):
    """在 PR 的特定行上發表評論 (不再是 Suggestion)"""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/comments"
    payload = {
        "body": body,
        "commit_id": commit_id,
        "path": path,
        "line": line
    }
    response = requests.post(url, headers=get_github_headers(token), json=payload)
    if response.status_code == 201:
        print(f"Successfully posted comment for {path} at line {line}.")
    else:
        print(f"Failed to post comment for {path} at line {line}. Status: {response.status_code}, Response: {response.text}")

def check_po_file(file_path, glossary, existing_comments):
    """檢查單一 PO 檔案，在句子中查找術語錯誤"""
    print(f"Checking file for sentence-based terms: {file_path}")
    
    po = polib.pofile(file_path, encoding='utf-8')
    comments_to_make = []

    for entry in po:
        if not entry.msgid or not entry.msgstr:
            continue

        # 遍歷整個術語表，在句子中查找術語
        for term in glossary:
            source_term = term['source']
            target_term = term['target']
            common_errors = term.get('common_errors', [])

            # 使用正則表達式進行全詞匹配，避免部分匹配 (e.g., 'cat' in 'caterpillar')
            # \b 是單詞邊界符
            if re.search(r'\b' + re.escape(source_term) + r'\b', entry.msgid, re.IGNORECASE):
                
                # 檢查是否存在常見錯誤，並且沒有正確翻譯
                found_error = None
                for error in common_errors:
                    if error in entry.msgstr:
                        found_error = error
                        break
                
                if found_error and target_term not in entry.msgstr:
                    # 找到 msgstr 所在的行號
                    with open(file_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                    msgstr_linenum = -1
                    for i in range(entry.linenum - 1, len(lines)):
                        if lines[i].strip().startswith('msgstr'):
                            msgstr_linenum = i + 1
                            break
                    
                    if msgstr_linenum == -1:
                        continue

                    if (file_path, msgstr_linenum) in existing_comments:
                        print(f"  Skipping already posted comment for '{source_term}' at line {msgstr_linenum}.")
                        continue

                    print(f"  [COMMENT] Found potential term error for '{source_term}' in a sentence at line {msgstr_linenum}.")
                    
                    message_body = (
                        f"**術語檢查提醒**：\n"
                        f"這句話中的原文 `{source_term}`，其翻譯 `{found_error}` 可能是個常見錯誤。\n"
                        f"建議的正確術語為：`{target_term}`。\n"
                        f"請手動檢查並修正此行。"
                    )
                    
                    comments_to_make.append({'path': file_path, 'line': msgstr_linenum, 'body': message_body})
                    # 找到一個錯誤後就跳出術語表循環，避免對同一行重複留言
                    break 

    return comments_to_make

def load_glossary_list(file_path):
    """載入術語表為列表格式"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

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

    print("Finding existing comments...")
    existing_comments = find_existing_comments(github_repo, pr_number, github_token)
    print(f"Found {len(existing_comments)} existing comments from this bot.")

    glossary = load_glossary_list(glossary_file)
    all_comments_to_make = []

    for po_file in po_files:
        if os.path.exists(po_file) and os.path.getsize(po_file) > 0:
            comments = check_po_file(po_file, glossary, existing_comments)
            all_comments_to_make.extend(comments)

    for comment in all_comments_to_make:
        post_line_comment(github_repo, pr_number, github_token, commit_id, comment['path'], comment['line'], comment['body'])

    if all_comments_to_make:
        print(f"\nFound {len(all_comments_to_make)} potential issues. Exiting with status 1 to fail the check.")
        sys.exit(1)
    else:
        print("\nNo new issues found. All good!")
        sys.exit(0)
        