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
        print(f"✅ Successfully posted suggestion for {path} at lines {start_line or ''}-{end_line}.")
    else:
        print(f"❌ Failed to post suggestion for {path}. Status: {response.status_code}, Response: {response.text}")

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

# [修改] 移除了 existing_comments 參數
def check_po_file(file_path, glossary_map, glossary_list):
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

        found_issue = False
        
        if entry.msgid in glossary_map:
            term_data = glossary_map[entry.msgid]
            correct_translation = term_data['target']
            if entry.msgstr != correct_translation:
                start_line, end_line = find_msgstr_line_range(lines, entry.linenum)
                # [修改] 移除了 if ... in existing_comments 的檢查
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

        for term in glossary_list:
            source_term, target_term = term['source'], term['target']
            errors_to_check = term.get('common_errors', [])
            if re.search(r'\b' + re.escape(source_term) + r'\b', entry.msgid, re.IGNORECASE):
                found_error = next((error for error in errors_to_check if re.search(re.escape(error), entry.msgstr, re.IGNORECASE)), None)
                if found_error and not re.search(re.escape(target_term), entry.msgstr, re.IGNORECASE):
                    start_line, end_line = find_msgstr_line_range(lines, entry.linenum)
                    # [修改] 移除了 if ... in existing_comments 的檢查
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
                    break 

    return comments_to_make

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

    # [修改] 移除了對 find_existing_bot_comments 的調用
    # print("\n🔄 Finding existing bot comments to avoid duplicates...")
    # existing_comments = find_existing_bot_comments(github_repo, pr_number, github_token)
    # print(f"  Found {len(existing_comments)} existing comments from this bot.")

    glossary_map, glossary_list = load_glossary(glossary_file)
    all_comments_to_make = []

    for po_file in po_files:
        if os.path.exists(po_file) and os.path.getsize(po_file) > 0:
            # [修改] 調用 check_po_file 時不再傳入 existing_comments
            comments = check_po_file(po_file, glossary_map, glossary_list)
            all_comments_to_make.extend(comments)
        else:
            print(f"  [SKIP] File not found or is empty: {po_file}")

    if all_comments_to_make:
        print(f"\n📮 Posting {len(all_comments_to_make)} new suggestions to the PR...")
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