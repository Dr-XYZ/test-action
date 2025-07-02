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

def find_existing_bot_comments(repo, pr_number, token):
    """查找 Bot 之前在特定行上發布的所有評論（包括 Suggestion），避免重複"""
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
    """在 PR 的特定行上發表評論或建議"""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/comments"
    payload = {
        "body": body,
        "commit_id": commit_id,
        "path": path,
        "line": line
    }
    response = requests.post(url, headers=get_github_headers(token), json=payload)
    if response.status_code == 201:
        print(f"Successfully posted comment/suggestion for {path} at line {line}.")
    else:
        print(f"Failed to post comment/suggestion for {path} at line {line}. Status: {response.status_code}, Response: {response.text}")

def check_po_file(file_path, glossary_map, glossary_list, existing_comments):
    """檢查 PO 檔案，同時處理完全匹配（Suggestion）和句子匹配（Comment）"""
    print(f"Checking file with hybrid strategy: {file_path}")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    po = polib.pofile(file_path, encoding='utf-8')
    comments_to_make = []

    for entry in po:
        if not entry.msgid or not entry.msgstr:
            continue

        # --- 策略 1: 完全匹配 (高信度 -> Suggestion) ---
        if entry.msgid in glossary_map:
            term_data = glossary_map[entry.msgid]
            correct_translation = term_data['target']
            
            if entry.msgstr != correct_translation:
                msgstr_linenum = find_msgstr_line(lines, entry.linenum)
                if msgstr_linenum == -1 or (file_path, msgstr_linenum) in existing_comments:
                    continue

                print(f"  [SUGGESTION] Found exact match error for '{entry.msgid}' at line {msgstr_linenum}.")
                
                original_line = lines[msgstr_linenum - 1]
                leading_whitespace = original_line[:len(original_line) - len(original_line.lstrip())]
                escaped_translation = correct_translation.replace('"', '\\"')
                suggested_line = f'{leading_whitespace}msgstr "{escaped_translation}"'
                
                reason = "是已知的常見錯誤" if entry.msgstr in term_data.get('errors', []) else "不符合術語表規範"
                
                message_body = (
                    f"術語 `{entry.msgid}` 的翻譯 `{entry.msgstr}` {reason}。\n"
                    f"建議更正為 `{correct_translation}`。\n"
                    f"```suggestion\n"
                    f"{suggested_line}\n"
                    f"```"
                )
                comments_to_make.append({'path': file_path, 'line': msgstr_linenum, 'body': message_body})
                continue # 處理完此條目，跳到下一個

        # --- 策略 2: 句子中包含術語 (低信度 -> Comment) ---
        for term in glossary_list:
            source_term, target_term = term['source'], term['target']
            common_errors = term.get('common_errors', [])

            if re.search(r'\b' + re.escape(source_term) + r'\b', entry.msgid, re.IGNORECASE):
                found_error = next((error for error in common_errors if error in entry.msgstr), None)
                
                if found_error and target_term not in entry.msgstr:
                    msgstr_linenum = find_msgstr_line(lines, entry.linenum)
                    if msgstr_linenum == -1 or (file_path, msgstr_linenum) in existing_comments:
                        continue

                    print(f"  [COMMENT] Found potential term error for '{source_term}' in a sentence at line {msgstr_linenum}.")
                    
                    message_body = (
                        f"**術語檢查提醒**：\n"
                        f"這句話中的原文 `{source_term}`，其翻譯可能包含了常見錯誤 `{found_error}`。\n"
                        f"建議的正確術語為：`{target_term}`。\n"
                        f"請手動檢查並修正此行。"
                    )
                    comments_to_make.append({'path': file_path, 'line': msgstr_linenum, 'body': message_body})
                    break # 找到一個錯誤就跳出術語循環

    return comments_to_make

def find_msgstr_line(lines, start_linenum):
    """從指定行號開始，找到對應的 msgstr 行號"""
    for i in range(start_linenum - 1, len(lines)):
        if lines[i].strip().startswith('msgstr'):
            return i + 1
    return -1

def load_glossary(file_path):
    """載入術語表，同時返回 map 和 list 兩種格式"""
    with open(file_path, 'r', encoding='utf-8') as f:
        glossary_list = json.load(f)
    glossary_map = {item['source']: item for item in glossary_list}
    return glossary_map, glossary_list

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

    print("Finding existing bot comments...")
    existing_comments = find_existing_bot_comments(github_repo, pr_number, github_token)
    print(f"Found {len(existing_comments)} existing comments from this bot.")

    glossary_map, glossary_list = load_glossary(glossary_file)
    all_comments_to_make = []

    for po_file in po_files:
        if os.path.exists(po_file) and os.path.getsize(po_file) > 0:
            comments = check_po_file(po_file, glossary_map, glossary_list, existing_comments)
            all_comments_to_make.extend(comments)

    for comment in all_comments_to_make:
        post_line_comment(github_repo, pr_number, github_token, commit_id, comment['path'], comment['line'], comment['body'])

    if all_comments_to_make:
        print(f"\nFound {len(all_comments_to_make)} issues. Exiting with status 1 to fail the check.")
        sys.exit(1)
    else:
        print("\nNo new issues found. All good!")
        sys.exit(0)