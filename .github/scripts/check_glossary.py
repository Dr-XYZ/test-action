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
    """查找 Bot 之前在特定行上發布的所有評論，避免重複"""
    headers = get_github_headers(token)
    existing_comments = set()
    page = 1
    while True:
        url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/comments?per_page=100&page={page}"
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            break
        comments_on_page = response.json()
        if not comments_on_page:
            break
        for comment in comments_on_page:
            if comment.get("user", {}).get("login") == "github-actions[bot]":
                path = comment.get("path")
                # [修改] 為了簡化，我們只用結束行來判斷是否重複
                line = comment.get("line") 
                if path and line:
                    existing_comments.add((path, line))
        page += 1
    return existing_comments

# [修改] 函數增加 start_line 參數以支持多行建議
def post_line_comment(repo, pr_number, token, commit_id, path, end_line, body, start_line=None):
    """在 PR 的特定行或行範圍上發表評論或建議"""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/comments"
    payload = {
        "body": body,
        "commit_id": commit_id,
        "path": path,
        "line": end_line, # 對於多行，這是範圍的結束行
    }
    # 如果提供了 start_line，則這是一個多行評論/建議
    if start_line and start_line != end_line:
        payload["start_line"] = start_line
        
    response = requests.post(url, headers=get_github_headers(token), json=payload)
    if response.status_code == 201:
        print(f"✅ Successfully posted suggestion for {path} at lines {start_line or ''}-{end_line}.")
    else:
        print(f"❌ Failed to post suggestion for {path}. Status: {response.status_code}, Response: {response.text}")

# [新增] 輔助函數，找到 msgstr 區塊的起始和結束行號
def find_msgstr_line_range(lines, start_linenum):
    """從指定行號開始，找到對應的 msgstr 區塊的起始和結束行號"""
    msgstr_start_line = -1
    msgstr_end_line = -1

    # 找到 msgstr 的起始行
    for i in range(start_linenum - 1, len(lines)):
        if lines[i].strip().startswith('msgstr'):
            msgstr_start_line = i + 1
            msgstr_end_line = i + 1
            break
    
    if msgstr_start_line == -1:
        return -1, -1

    # 從起始行開始，繼續尋找多行 msgstr 的結束
    for i in range(msgstr_start_line, len(lines)):
        line_content = lines[i].strip()
        if line_content.startswith('"') and line_content.endswith('"'):
            msgstr_end_line = i + 1
        else:
            break
            
    return msgstr_start_line, msgstr_end_line

# [新增] 輔助函數，將字符串格式化為 PO 檔案中的 msgstr 格式
def format_msgstr_for_suggestion(text, leading_whitespace):
    """將修正後的文本格式化為 PO 檔案的 suggestion 語法"""
    # 轉義 " 和 \
    escaped_text = text.replace('\\', '\\\\').replace('"', '\\"')
    lines = escaped_text.split('\n')
    
    if len(lines) == 1:
        return f'{leading_whitespace}msgstr "{lines[0]}"'
    else:
        formatted_lines = [f'{leading_whitespace}msgstr ""']
        for line in lines:
            formatted_lines.append(f'{leading_whitespace}"{line}\\n"')
        # 移除最後一行的 \n
        formatted_lines[-1] = formatted_lines[-1][:-3] + '"'
        return '\n'.join(formatted_lines)

def check_po_file(file_path, glossary_map, glossary_list, existing_comments):
    """檢查 PO 檔案，對所有可確定的錯誤都提出 Suggestion"""
    print(f"\n🔎 Checking file: {file_path}")
    
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

        # 統一處理邏輯，先檢查完全匹配，再檢查句子匹配
        found_issue = False
        
        # --- 策略 1: 完全匹配 (高信度) ---
        if entry.msgid in glossary_map:
            term_data = glossary_map[entry.msgid]
            correct_translation = term_data['target']
            
            if entry.msgstr != correct_translation:
                start_line, end_line = find_msgstr_line_range(lines, entry.linenum)
                if end_line == -1 or (file_path, end_line) in existing_comments:
                    continue

                print(f"  [SUGGESTION] Found exact match error for '{entry.msgid}' at lines {start_line}-{end_line}.")
                
                original_line = lines[start_line - 1]
                leading_whitespace = original_line[:len(original_line) - len(original_line.lstrip())]
                suggested_block = format_msgstr_for_suggestion(correct_translation, leading_whitespace)
                
                errors_to_check = term_data.get('common_errors', [])
                reason = "是已知的常見錯誤" if entry.msgstr in errors_to_check else "不符合術語表規範"
                
                message_body = (
                    f"術語 `{entry.msgid}` 的翻譯 `{entry.msgstr}` {reason}。\n"
                    f"建議更正為 `{correct_translation}`。\n"
                    f"```suggestion\n"
                    f"{suggested_block}\n"
                    f"```"
                )
                comments_to_make.append({'path': file_path, 'start_line': start_line, 'end_line': end_line, 'body': message_body})
                found_issue = True

        if found_issue:
            continue

        # --- 策略 2: 句子中包含術語 (現在也提供 Suggestion) ---
        for term in glossary_list:
            source_term, target_term = term['source'], term['target']
            errors_to_check = term.get('common_errors', [])

            if re.search(r'\b' + re.escape(source_term) + r'\b', entry.msgid, re.IGNORECASE):
                # [修改] 移除 \b，直接搜索字符串，並忽略大小寫
                found_error = next((error for error in errors_to_check if re.search(re.escape(error), entry.msgstr, re.IGNORECASE)), None)
                
                if found_error and not re.search(re.escape(target_term), entry.msgstr, re.IGNORECASE):
                    start_line, end_line = find_msgstr_line_range(lines, entry.linenum)
                    if end_line == -1 or (file_path, end_line) in existing_comments:
                        continue

                    print(f"  [SUGGESTION] Found sentence term error for '{source_term}' at lines {start_line}-{end_line}.")
                    
                    # [修改] 生成修正後的句子
                    corrected_msgstr = entry.msgstr.replace(found_error, target_term)
                    
                    original_line = lines[start_line - 1]
                    leading_whitespace = original_line[:len(original_line) - len(original_line.lstrip())]
                    suggested_block = format_msgstr_for_suggestion(corrected_msgstr, leading_whitespace)

                    message_body = (
                        f"此句中的術語 `{found_error}` 可能是 `{source_term}` 的不正確翻譯。\n"
                        f"建議修正為 `{target_term}`。\n"
                        f"```suggestion\n"
                        f"{suggested_block}\n"
                        f"```"
                    )
                    comments_to_make.append({'path': file_path, 'start_line': start_line, 'end_line': end_line, 'body': message_body})
                    # 找到一個錯誤就跳出，避免對同一行提多個建議
                    break 

    return comments_to_make

# ... (load_glossary 和 main 函數保持不變，但 main 函數中的調用需要修改)

if __name__ == "__main__":
    # ... (前面的參數獲取和環境變量檢查不變) ...
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
        print(f"\n📮 Posting {len(all_comments_to_make)} new suggestions to the PR...")
        for comment in all_comments_to_make:
            # [修改] 調用 post_line_comment 時傳入 start_line 和 end_line
            post_line_comment(
                github_repo, pr_number, github_token, commit_id, 
                comment['path'], comment['end_line'], comment['body'], comment['start_line']
            )
        
        print(f"\n💥 Found {len(all_comments_to_make)} issues. Exiting with status 1 to fail the check.")
        sys.exit(1)
    else:
        print("\n✅ No new issues found. All good!")
        sys.exit(0)