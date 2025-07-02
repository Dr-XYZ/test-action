import sys
import json
import polib
import os

def load_glossary(filepath):
    """
    從 JSON 檔案載入術語表。
    返回一個字典，鍵是錯誤翻譯，值是包含原文和正確翻譯的字典。
    例如: {'合併請求': {'source': 'Pull Request', 'correct': '拉取请求'}}
    """
    mistake_map = {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            glossary_data = json.load(f)
            
        for term in glossary_data:
            source = term.get('source')
            correct = term.get('correct')
            mistakes = term.get('mistakes', [])
            
            if not source or not correct:
                continue
            
            for mistake in mistakes:
                if mistake: # 確保 mistake 不是空字串
                    mistake_map[mistake] = {'source': source, 'correct': correct}
                    
    except FileNotFoundError:
        print(f"Error: Glossary file not found at {filepath}")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON in glossary file at {filepath}")
        sys.exit(1)
        
    return mistake_map

def check_po_file(filepath, mistake_map):
    """檢查單一 .po 檔案，並返回建議列表"""
    suggestions = []
    try:
        po = polib.pofile(filepath, encoding='utf-8')
        for entry in po.translated_entries():
            if not entry.msgstr: # 忽略未翻譯的條目
                continue

            original_translation = entry.msgstr
            modified_translation = original_translation
            found_mistakes = []

            # 找出此條目中所有錯誤術語
            for mistake, terms in mistake_map.items():
                if mistake in modified_translation:
                    # 進行替換，產生建議的翻譯
                    modified_translation = modified_translation.replace(mistake, terms['correct'])
                    found_mistakes.append(f"檢測到術語 **`{mistake}`**，建議更正為 **`{terms['correct']}`**。")
            
            # 如果翻譯內容被修改，代表找到了錯誤
            if original_translation != modified_translation:
                # 建立一個清晰的評論，列出所有發現的問題
                comment_body = (
                    "術語建議修正：\n"
                    + "\n".join([f"- {fm}" for fm in found_mistakes])
                    + f"\n- 相關原文：`{entry.msgid}`"
                )
                
                # `suggester` action 需要的格式
                suggestion = {
                    "file": filepath,
                    "start_line": entry.linenum,
                    "end_line": entry.linenum,
                    "suggestion": f'msgstr "{modified_translation}"',
                    "comment": comment_body
                }
                suggestions.append(suggestion)

    except Exception as e:
        print(f"Error processing file {filepath}: {e}")
        
    return suggestions

def main():
    if len(sys.argv) < 3:
        print("Usage: python check_po_glossary.py <glossary.json> <po_file1> <po_file2> ...")
        sys.exit(1)

    glossary_path = sys.argv[1]
    po_files = sys.argv[2:]

    mistake_map = load_glossary(glossary_path)
    all_suggestions = []

    for po_file in po_files:
        if os.path.exists(po_file):
            all_suggestions.extend(check_po_file(po_file, mistake_map))
        else:
            print(f"Warning: File not found, skipping: {po_file}")


    if all_suggestions:
        # 輸出成 JSON 檔案給 GitHub Action 使用
        with open('suggestions.json', 'w', encoding='utf-8') as f:
            json.dump(all_suggestions, f, ensure_ascii=False, indent=2)
        
        # 設置 GitHub Action 的輸出變數，表示找到了建議
        if 'GITHUB_OUTPUT' in os.environ:
            with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
                f.write('has_suggestions=true\n')
        print(f"Found {len(all_suggestions)} suggestions. See suggestions.json")
    else:
        print("No glossary issues found.")

if __name__ == "__main__":
    main()