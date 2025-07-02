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
    """
    查找 Bot 之前在特定行上發布的所有評論（包括 Suggestion），避免重複。
    [修改] 增加了分頁處理邏輯，以獲取所有評論。
    """
    headers = get_github_headers(token)
    existing_comments = set()
    page = 1
    while True: # <--- 修改點：使用 while 迴圈處理分頁
        url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/comments?per_page=100&page={page}"
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            print(f"Warning: Failed to fetch comments (page {page}). Status: {response.status_code}")
            break

        comments_on_page = response.json()
        if not comments_on_page: # <--- 修改點：如果當前頁面沒有評論，則停止
            break

        for comment in comments_on_page:
            # 使用 get 方法避免因缺少鍵而引發的 KeyError
            if comment.get("user", {}).get("login") == "github-actions[bot]":
                path = comment.get("path")
                line = comment.get("line")
                if path and line:
                    existing_comments.add((path, line))
        
        page += 1 # <--- 修改點：進入下一頁
        
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
        print(f"✅ Successfully posted comment/suggestion for {path} at line {line}.")
    else:
        print(f"❌ Failed to post comment/suggestion for {path} at line {line}. Status: {response.status_code}, Response: {response.text}")

def check_po_file(file_path, glossary_map, glossary_list, existing_comments):
    """檢查 PO 檔案，同時處理完全匹配（Suggestion）和句子匹配（Comment）"""
    print(f"\n🔎 Checking file with hybrid strategy: {file_path}")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        po = polib.pofile(file_path, encoding='utf-8')
    except Exception as e:
        print(f"  [ERROR] Could not read or parse {file_path}: {e}")
        return []

    comments_to_make = []

    for entry in po:
        if not entry.msgid or not entry.msgstr or entry.obsolete:
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
                
                # <--- 修改點：將 'errors' 修正為 'common_errors' 以匹配 JSON
                errors_to_check = term_data.get('common_errors', [])
                reason = "是已知的常見錯誤" if entry.msgstr in errors_to_check else "不符合術語表規範"
                
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
            errors_to_check = term.get('common_errors', [])

            # 使用全詞匹配檢查原文術語是否存在
            if re.search(r'\b' + re.escape(source_term) + r'\b', entry.msgid, re.IGNORECASE):
                # <--- 修改點：使用正則表達式進行全詞匹配，避免誤判
                found_error = next((error for error in errors_to_check if re.search(r'\b' + re.escape(error) + r'\b', entry.msgstr, re.IGNORECASE)), None)
                
                # 只有在找到常見錯誤，且正確翻譯不存在時才提醒
                if found_error and not re.search(r'\b' + re.escape(target_term) + r'\b', entry.msgstr, re.IGNORECASE):
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
                    # 為了避免對同一行產生多個術語的提醒，找到一個就跳出
                    break 

    return comments_to_make

def find_msgstr_line(lines, start_linenum):
    """從指定行號開始，找到對應的 msgstr 行號"""
    # 確保不會超出索引範圍
    for i in range(start_linenum - 1, len(lines)):
        if lines[i].strip().startswith('msgstr'):
            return i + 1
    return -1

def load_glossary(file_path):
    """載入術語表，同時返回 map 和 list 兩種格式"""
    print(f"📖 Loading glossary from {file_path}...")
    with open(file_path, 'r', encoding='utf-8') as f:
        glossary_list = json.load(f)
    glossary_map = {item['source']: item for item in glossary_list}
    print(f"  Loaded {len(glossary_list)} terms.")
    return glossary_map, glossary_list

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python check_glossary.py <glossary_file> [po_file1 po_file2 ...]")
        sys.exit(1)

    glossary_file = sys.argv[1]
    po_files = sys.argv[2:]

    if not po_files:
        print("No PO files to check. Exiting.")
        sys.exit(0)

    github_token = os.environ.get("GITHUB_TOKEN")
    github_repo = os.environ.get("GITHUB_REPOSITORY")
    pr_number = os.environ.get("PR_NUMBER")
    commit_id = os.environ.get("COMMIT_ID")

    if not all([github_token, github_repo, pr_number, commit_id]):
        print("❌ Error: Missing required GitHub environment variables.")
        sys.exit(1)

    print("🤖 Starting glossary check process...")
    print(f"Repository: {github_repo}, PR: #{pr_number}")

    print("\n🔄 Finding existing bot comments to avoid duplicates...")
    existing_comments = find_existing_bot_comments(github_repo, pr_number, github_token)
    print(f"  Found {len(existing_comments)} existing comments from this bot.")

    glossary_map, glossary_list = load_glossary(glossary_file)
    all_comments_to_make = []

    for po_file in po_files:
        if os.path.exists(po_file) and os.path.getsize(po_file) > 0:
            comments = check_po_file(po_file, glossary_map, glossary_list, existing_comments)
            all_comments_to_make.extend(comments)
        else:
            print(f"  [SKIP] File not found or is empty: {po_file}")

    if all_comments_to_make:
        print(f"\n📮 Posting {len(all_comments_to_make)} new comments/suggestions to the PR...")
        for comment in all_comments_to_make:
            post_line_comment(github_repo, pr_number, github_token, commit_id, comment['path'], comment['line'], comment['body'])
        
        print(f"\n💥 Found {len(all_comments_to_make)} issues. Exiting with status 1 to fail the check.")
        sys.exit(1)
    else:
        print("\n✅ No new issues found. All good!")
        sys.exit(0)
        