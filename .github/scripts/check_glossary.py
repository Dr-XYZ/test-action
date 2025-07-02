import os
import sys
import json
import polib
import requests
import re

# ... (get_github_headers, post_line_comment, find_msgstr_line_range, format_msgstr_for_suggestion 函數保持不變) ...
def get_github_headers(token):
    """生成 GitHub API 請求標頭"""
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

def post_line_comment(repo, pr_number, token, commit_id, path, end_line, body, start_line=None):
    """在 PR 的特定行或行範圍上發表評論或建議"""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/comments"
    payload = {
        "body": body,
        "commit_id": commit_id,
        "path": path,
        "line": end_line,
    }
    if start_line and start_line != end_line:
        payload["start_line"] = start_line
        
    response = requests.post(url, headers=get_github_headers(token), json=payload)
    if response.status_code == 201:
        print(f"✅ Successfully posted comment/suggestion for {path} at lines {start_line or ''}-{end_line}.")
    else:
        print(f"❌ Failed to post comment/suggestion for {path}. Status: {response.status_code}, Response: {response.text}")

def find_msgstr_line_range(lines, start_linenum):
    """從指定行號開始，找到對應的 msgstr 區塊的起始和結束行號"""
    msgstr_start_line, msgstr_end_line = -1, -1
    for i in range(start_linenum - 1, len(lines)):
        if lines[i].strip().startswith('msgstr'):
            msgstr_start_line = i + 1
            msgstr_end_line = i + 1
            break
    if msgstr_start_line == -1: return -1, -1
    for i in range(msgstr_start_line, len(lines)):
        line_content = lines[i].strip()
        if line_content.startswith('"') and line_content.endswith('"'):
            msgstr_end_line = i + 1
        else:
            break
    return msgstr_start_line, msgstr_end_line

def format_msgstr_for_suggestion(text, leading_whitespace):
    """將修正後的文本格式化為 PO 檔案的 suggestion 語法"""
    escaped_text = text.replace('\\', '\\\\').replace('"', '\\"')
    lines = escaped_text.split('\n')
    if len(lines) == 1:
        return f'{leading_whitespace}msgstr "{lines[0]}"'
    else:
        formatted_lines = [f'{leading_whitespace}msgstr ""']
        for line in lines:
            formatted_lines.append(f'{leading_whitespace}"{line}\\n"')
        formatted_lines[-1] = formatted_lines[-1][:-3] + '"'
        return '\n'.join(formatted_lines)

def check_po_file(file_path, glossary_map, glossary_list):
    """檢查 PO 檔案，區分 Suggestion 和 Comment"""
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

        found_issue = False
        
        # 策略 1: 完全匹配錯誤 -> Suggestion
        if entry.msgid in glossary_map:
            term_data = glossary_map[entry.msgid]
            correct_translation = term_data['target']
            if entry.msgstr != correct_translation:
                start_line, end_line = find_msgstr_line_range(lines, entry.linenum)
                if end_line == -1: continue

                print(f"  [SUGGESTION] Found exact match error for '{entry.msgid}' at lines {start_line}-{end_line}.")
                original_line = lines[start_line - 1]
                leading_whitespace = original_line[:len(original_line) - len(original_line.lstrip())]
                suggested_block = format_msgstr_for_suggestion(correct_translation, leading_whitespace)
                errors_to_check = term_data.get('common_errors', [])
                reason = "是已知的常見錯誤" if entry.msgstr in errors_to_check else "不符合術語表規範"
                message_body = (
                    f"術語 `{entry.msgid}` 的翻譯 `{entry.msgstr}` {reason}。\n"
                    f"建議更正為 `{correct_translation}`。\n"
                    f"```suggestion\n{suggested_block}\n```"
                )
                comments_to_make.append({'path': file_path, 'start_line': start_line, 'end_line': end_line, 'body': message_body})
                found_issue = True

        if found_issue: continue

        # 策略 2 & 3: 句子匹配
        for term in glossary_list:
            source_term, target_term = term['source'], term['target']
            errors_to_check = term.get('common_errors', [])

            # 檢查原文是否包含術語
            if re.search(r'\b' + re.escape(source_term) + r'\b', entry.msgid, re.IGNORECASE):
                
                # 檢查譯文是否包含正確術語
                target_term_present = re.search(re.escape(target_term), entry.msgstr, re.IGNORECASE)
                
                # 檢查譯文是否包含常見錯誤
                found_error = next((error for error in errors_to_check if re.search(re.escape(error), entry.msgstr, re.IGNORECASE)), None)

                # 策略 2: 包含常見錯誤 -> Suggestion
                if found_error and not target_term_present:
                    start_line, end_line = find_msgstr_line_range(lines, entry.linenum)
                    if end_line == -1: continue

                    print(f"  [SUGGESTION] Found sentence term error for '{source_term}' at lines {start_line}-{end_line}.")
                    corrected_msgstr = entry.msgstr.replace(found_error, target_term)
                    original_line = lines[start_line - 1]
                    leading_whitespace = original_line[:len(original_line) - len(original_line.lstrip())]
                    suggested_block = format_msgstr_for_suggestion(corrected_msgstr, leading_whitespace)
                    message_body = (
                        f"此句中的術語 `{found_error}` 可能是 `{source_term}` 的不正確翻譯。\n"
                        f"建議修正為 `{target_term}`。\n"
                        f"```suggestion\n{suggested_block}\n```"
                    )
                    comments_to_make.append({'path': file_path, 'start_line': start_line, 'end_line': end_line, 'body': message_body})
                    found_issue = True
                    break # 找到一個問題就處理，跳出術語循環
                
                # [新增] 策略 3: 未使用正確術語，也未使用常見錯誤 -> Comment
                elif not target_term_present and not found_error:
                    start_line, end_line = find_msgstr_line_range(lines, entry.linenum)
                    if end_line == -1: continue

                    print(f"  [COMMENT] Found potential missing term for '{source_term}' at lines {start_line}-{end_line}.")
                    
                    message_body = (
                        f"**術語檢查提醒 (低信度)**：\n"
                        f"- 原文中包含了術語：`{source_term}`\n"
                        f"- 但在譯文中未找到推薦的術語：`{target_term}`\n\n"
                        f"請手動檢查當前的翻譯是否準確，或考慮使用推薦術語。"
                    )
                    comments_to_make.append({'path': file_path, 'start_line': start_line, 'end_line': end_line, 'body': message_body})
                    found_issue = True
                    break # 找到一個問題就處理，跳出術語循環

    return comments_to_make

# ... (load_glossary 和 __main__ 函數保持不變) ...
def load_glossary(file_path):
    """載入術語表"""
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

    glossary_map, glossary_list = load_glossary(glossary_file)
    all_comments_to_make = []

    for po_file in po_files:
        if os.path.exists(po_file) and os.path.getsize(po_file) > 0:
            comments = check_po_file(po_file, glossary_map, glossary_list)
            all_comments_to_make.extend(comments)
        else:
            print(f"  [SKIP] File not found or is empty: {po_file}")

    if all_comments_to_make:
        print(f"\n📮 Posting {len(all_comments_to_make)} new comments/suggestions to the PR...")
        for comment in all_comments_to_make:
            post_line_comment(
                github_repo, pr_number, github_token, commit_id, 
                comment['path'], comment['end_line'], comment['body'], comment['start_line']
            )
        
        print(f"\n💥 Found {len(all_comments_to_make)} issues. Exiting with status 1 to fail the check.")
        sys.exit(1)
    else:
        print("\n✅ No new issues found. All good!")
        sys.exit(0)